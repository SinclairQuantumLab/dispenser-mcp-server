"""Idle-first web runtime with explicit checked run binding."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import tomllib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, cast

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import (
    CallToolResult,
    InputRequiredResult,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import BaseRoute, Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from dispenser_conditioning_mcp.config import (
    ConfigurationError,
    SourceLayout,
)
from dispenser_conditioning_mcp.dashboard import dashboard_routes
from dispenser_conditioning_mcp.dashboard_access import DashboardAccess
from dispenser_conditioning_mcp.learning_mode import (
    AGENT_INSTRUMENT_MUTATIONS,
    LEARNING_AGENT_TOOLS,
    SAVED_LEARNING_AGENT_TOOLS,
    HumanManualOperation,
    HumanNoteRequest,
    LearningArtifactStore,
    LearningModeService,
    ReadReviewsRequest,
)
from dispenser_conditioning_mcp.operator_settings import (
    AuthorityMode,
    OperatorConsoleSettings,
    OperatorSettingsStore,
    OperatorSettingsUpdate,
)
from dispenser_conditioning_mcp.optional_capabilities import OptionalCapabilityRegistry
from dispenser_conditioning_mcp.recording_service import error_result
from dispenser_conditioning_mcp.run_history import HISTORY_TOOLS, RunHistory
from dispenser_conditioning_mcp.transport import McpTransportConfiguration

LOGGER = logging.getLogger(__name__)
READ_SERVER_STATUS_TOOL = "read_server_status"
HIDDEN_OPERATOR_TOOL = "reload_dispenser_current_limit"
MAX_OPERATOR_BODY_BYTES = 256 * 1024
TAKEOVER_CONFIRMATION = "confirmed_operator_takeover"


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(min_length=1, max_length=128)


class StartRunRequest(RevisionRequest):
    check_id: str = Field(min_length=1, max_length=256)


class StopRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_binding: str = Field(min_length=1, max_length=256)
    acknowledge_no_automatic_output_change: bool


class UpdateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_binding: str = Field(min_length=1, max_length=256)
    authority_mode: AuthorityMode | None = None
    sample_interval_s: float | None = Field(default=None, ge=0.1, le=3600.0)

    @model_validator(mode="after")
    def require_change(self) -> UpdateRunRequest:
        if self.authority_mode is None and self.sample_interval_s is None:
            raise ValueError("At least one run policy field is required")
        return self


@dataclass(frozen=True)
class CheckReceipt:
    check_id: str
    revision: str
    backend: str
    checked_at: str
    checks: tuple[dict[str, Any], ...]


@dataclass
class ActiveRun:
    server: MCPServer[None]
    learning: LearningModeService
    run_binding: str
    backend: str
    authority_mode: AuthorityMode
    settings_revision: str
    started_at: str


@dataclass(frozen=True)
class SavedRunAccess:
    """Offline readers for one real saved run; owns no device backend."""

    directory: Path
    history: RunHistory
    artifacts: LearningArtifactStore


type CandidateChecker = Callable[
    [OperatorConsoleSettings], Awaitable[tuple[dict[str, Any], ...]]
]
type CandidateStarter = Callable[[OperatorConsoleSettings], Awaitable[MCPServer[None]]]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RuntimeCoordinator:
    """Own one explicit active run and serialize authority/lifecycle changes."""

    def __init__(
        self,
        *,
        layout: SourceLayout | None = None,
        settings_store: OperatorSettingsStore | None = None,
        checker: CandidateChecker | None = None,
        starter: CandidateStarter | None = None,
        capabilities: OptionalCapabilityRegistry | None = None,
        access: DashboardAccess | None = None,
    ) -> None:
        self.layout = layout or SourceLayout.discover()
        self.settings_store = settings_store or OperatorSettingsStore(self.layout)
        self._checker = checker or self._check_candidate
        self._starter = starter or self._start_candidate
        self.capabilities = capabilities or OptionalCapabilityRegistry()
        self.access = access or DashboardAccess()
        self._operation_lock = asyncio.Lock()
        self._active: ActiveRun | None = None
        self._last: ActiveRun | None = None
        self._saved: SavedRunAccess | None = None
        self._check: CheckReceipt | None = None
        self.mcp = RuntimeMCPServer(self)
        self.dashboard = RuntimeDashboardApplication(self)

    @property
    def active(self) -> ActiveRun | None:
        return self._active

    @property
    def selected(self) -> ActiveRun | None:
        return self._active or self._last

    def public_status(self) -> dict[str, Any]:
        active = self._active
        learning = active.learning if active is not None else None
        capabilities = self.capabilities.public_descriptors()
        run = None
        if active is not None:
            run = {
                "run_binding": active.run_binding,
                "run_key": "",
                "backend": active.backend,
                "authority_mode": active.authority_mode,
                "started_at": active.started_at,
                "capabilities": capabilities,
            }
        return {
            "lifecycle": "running" if active is not None else "idle",
            "config_revision": active.settings_revision if active else None,
            "active_run": run,
            "last_run_available": self._last is not None
            or self.saved_run_access() is not None,
            "sampler": {
                "running": learning.running if learning else False,
                "interval_s": (
                    learning.configuration.sample_interval_s if learning else None
                ),
                "last_sample_at": learning.last_sample_at if learning else None,
                "last_error": learning.last_error if learning else None,
            },
            "agent_permissions": {
                "instrument_mutations": bool(
                    active is not None
                    and active.authority_mode == "agent_control"
                    and active.learning.control_enabled
                ),
                "metadata_reads": True,
                "review_append": self.selected is not None,
            },
            "capabilities": capabilities,
        }

    def operator_settings(self) -> dict[str, Any]:
        settings, revision = self.settings_store.load()
        suggested = None if settings is not None else self.settings_store.suggested()
        return {
            "configured": settings is not None,
            "revision": revision if settings is not None else None,
            "settings": (
                settings.model_dump(mode="json") if settings is not None else None
            ),
            "suggested": suggested,
            "auth_file_present": self.settings_store.auth_present(),
        }

    async def save_settings(self, update: OperatorSettingsUpdate) -> dict[str, Any]:
        async with self._operation_lock:
            settings, revision = await asyncio.to_thread(
                self.settings_store.save, update, active=self._active is not None
            )
            self._check = None
            return {
                "configured": True,
                "revision": revision,
                "settings": settings.model_dump(mode="json"),
                "suggested": None,
                "auth_file_present": self.settings_store.auth_present(),
            }

    async def check(self, revision: str) -> dict[str, Any]:
        async with self._operation_lock:
            if self._active is not None:
                raise ConfigurationError(
                    "Stop the active run before checking another configuration."
                )
            settings, current_revision = await asyncio.to_thread(
                self.settings_store.load
            )
            if settings is None or revision != current_revision:
                raise ConfigurationError(
                    "Saved settings changed or are unavailable; reload and save before checking."
                )
            checks = await self._checker(settings)
            receipt = CheckReceipt(
                check_id=secrets.token_urlsafe(24),
                revision=current_revision,
                backend=settings.backend,
                checked_at=_utc_now(),
                checks=checks,
            )
            self._check = receipt
            return {
                "ok": True,
                "check_id": receipt.check_id,
                "revision": receipt.revision,
                "backend": receipt.backend,
                "checked_at": receipt.checked_at,
                "checks": list(receipt.checks),
            }

    async def start_run(self, request: StartRunRequest) -> dict[str, Any]:
        async with self._operation_lock:
            if self._active is not None:
                raise ConfigurationError("A run is already active.")
            receipt = self._check
            if (
                receipt is None
                or request.check_id != receipt.check_id
                or request.revision != receipt.revision
            ):
                raise ConfigurationError(
                    "Run start requires the latest successful check for this saved revision."
                )
            settings, revision = await asyncio.to_thread(self.settings_store.load)
            if settings is None or revision != receipt.revision:
                self._check = None
                raise ConfigurationError(
                    "Saved settings changed after the connection check; check again."
                )
            server = await self._starter(settings)
            learning = getattr(server, "learning", None)
            if not isinstance(learning, LearningModeService):
                raise RuntimeError(
                    "The selected backend did not provide acquisition ownership"
                )
            active = ActiveRun(
                server=server,
                learning=learning,
                run_binding=secrets.token_urlsafe(32),
                backend=settings.backend,
                authority_mode=settings.authority_mode,
                settings_revision=revision,
                started_at=_utc_now(),
            )
            self._active = active
            self._last = active
            self._check = None
            await learning.start()
            return self.public_status()

    async def update_run(self, request: UpdateRunRequest) -> dict[str, Any]:
        async with self._operation_lock:
            active = self._require_binding(request.run_binding)
            await active.learning.stop()
            try:
                settings, revision = await asyncio.to_thread(
                    self.settings_store.save_run_policy,
                    expected_revision=active.settings_revision,
                    authority_mode=request.authority_mode,
                    sample_interval_s=request.sample_interval_s,
                )
                active.authority_mode = settings.authority_mode
                active.settings_revision = revision
                active.learning.set_configuration(settings.learning)
            finally:
                await active.learning.start()
            return self.public_status()

    async def stop_run(self, request: StopRunRequest) -> dict[str, Any]:
        if request.acknowledge_no_automatic_output_change is not True:
            raise ConfigurationError(
                "Stopping a run requires acknowledgement that no output change occurs."
            )
        async with self._operation_lock:
            active = self._require_binding(request.run_binding)
            await active.learning.stop()
            active.learning.mark_saved()
            history = getattr(active.server, "history", None)
            if history is not None:
                history.mark_saved()
            self._last = active
            self._active = None
            return self.public_status()

    async def shutdown_lifecycle(self) -> None:
        """Stop acquisition only; process shutdown never actuates instruments."""

        async with self._operation_lock:
            if self._active is not None:
                await self._active.learning.stop()

    async def dispatch_agent(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None,
    ) -> CallToolResult | InputRequiredResult:
        async with self._operation_lock:
            active = self._active
            selected = active or self._last
            if selected is None:
                saved = self.saved_run_access()
                if saved is not None and name in HISTORY_TOOLS:
                    return await saved.history.call(name, arguments)
                if saved is not None and name in SAVED_LEARNING_AGENT_TOOLS:
                    return await saved.artifacts.call_agent_tool(name, arguments)
                return error_result(
                    "Not executed: no checked run is active or available."
                )
            if active is None and name not in HISTORY_TOOLS | LEARNING_AGENT_TOOLS:
                return error_result(
                    "Not executed: instrument reads and run declarations require an active run."
                )
            clean = dict(arguments)
            if name in AGENT_INSTRUMENT_MUTATIONS:
                binding = clean.pop("run_binding", None)
                if active is None or binding != active.run_binding:
                    return error_result(
                        "Not executed: run_binding is missing, stale, or not active."
                    )
                if active.authority_mode != "agent_control":
                    return error_result(
                        "Not executed: the authenticated operator currently owns instrument control."
                    )
            elif "run_binding" in clean:
                return error_result(
                    "Not executed: run_binding is unsupported for this tool."
                )
            return await selected.server.call_tool(name, clean, context)

    async def human_operation(self, request: HumanManualOperation) -> CallToolResult:
        async with self._operation_lock:
            active = self._require_binding(request.run_binding)
            if request.run:
                return error_result(
                    "Not executed: human instrument controls apply only to the current run."
                )
            if active.authority_mode == "agent_control":
                if request.operator_takeover_confirmation != TAKEOVER_CONFIRMATION:
                    return error_result(
                        "Not executed: explicit operator takeover confirmation is required."
                    )
                await active.learning.stop()
                try:
                    settings, revision = await asyncio.to_thread(
                        self.settings_store.save_run_policy,
                        expected_revision=active.settings_revision,
                        authority_mode="human_learning",
                    )
                    active.authority_mode = "human_learning"
                    active.settings_revision = revision
                    active.learning.set_configuration(settings.learning)
                finally:
                    await active.learning.start()
            return await active.learning.human_operation(request)

    def learning_snapshot(
        self,
        *,
        run_key: str,
        review_after: int,
        review_limit: int,
        note_after: int,
        note_limit: int,
        operator_authorized: bool,
    ) -> dict[str, Any]:
        selected = self.selected
        if selected is None:
            saved = self.saved_run_access()
            if saved is not None:
                return {
                    "enabled": False,
                    "current": False,
                    "operator_authorized": operator_authorized,
                    "sample_interval_s": None,
                    "running": False,
                    "last_sample_at": None,
                    "last_error": None,
                    "acceptance_context": None,
                    "power_state": saved.artifacts.latest_power_state(run_key),
                    "control_enabled": False,
                    "run_binding": None,
                    "authority_mode": None,
                    "human_control_available": False,
                    "human_notes": saved.artifacts.notes(
                        run_key, after=note_after, limit=note_limit
                    ),
                    "reviews": saved.artifacts.reviews(
                        run_key, after=review_after, limit=review_limit
                    ),
                }
            return {
                "enabled": False,
                "current": False,
                "operator_authorized": operator_authorized,
                "sample_interval_s": None,
                "running": False,
                "last_sample_at": None,
                "last_error": None,
                "acceptance_context": None,
                "power_state": None,
                "control_enabled": False,
                "run_binding": None,
                "authority_mode": None,
                "human_control_available": False,
                "human_notes": {"items": [], "after": 0, "has_more": False},
                "reviews": {"items": [], "after": 0, "has_more": False},
            }
        value = selected.learning.dashboard_snapshot(
            run_key=run_key,
            review_after=review_after,
            review_limit=review_limit,
            note_after=note_after,
            note_limit=note_limit,
            operator_authorized=operator_authorized,
        )
        active = self._active is selected
        value.update(
            {
                "enabled": bool(active and selected.authority_mode == "human_learning"),
                "run_binding": selected.run_binding if active else None,
                "authority_mode": selected.authority_mode if active else None,
                "human_control_available": bool(
                    active and selected.learning.control_enabled
                ),
                "current": bool(active and value.get("current")),
            }
        )
        return value

    def saved_run_access(self) -> SavedRunAccess | None:
        """Open bounded saved-run readers without allocating a run or backend."""

        if self._saved is not None:
            return self._saved
        directory = _latest_recorded_run(self.layout.project_root / "runs")
        if directory is None:
            return None
        history = RunHistory(directory)
        history.mark_saved()
        artifacts = LearningArtifactStore(directory)
        artifacts.mark_saved()
        self._saved = SavedRunAccess(directory, history, artifacts)
        return self._saved

    def selected_artifacts(self) -> LearningArtifactStore | None:
        selected = self.selected
        if selected is not None:
            return selected.learning.artifacts
        saved = self.saved_run_access()
        return saved.artifacts if saved is not None else None

    def _require_binding(self, binding: str | None) -> ActiveRun:
        if self._active is None or binding != self._active.run_binding:
            raise ConfigurationError(
                "The run binding is missing, stale, or not active."
            )
        return self._active

    async def _check_candidate(
        self, settings: OperatorConsoleSettings
    ) -> tuple[dict[str, Any], ...]:
        if settings.backend == "simulation":
            config = settings.simulator_configuration()
            from dispenser_simulator.server import build_runtime

            router, _ = build_runtime(config)
            router.call("read_vacuum_pressure", {"elapsed_s": 0})
            router.call("read_dispenser_power_state", {"elapsed_s": 0})
            return ({"name": "simulation_model", "ok": True},)

        configuration = settings.hardware_configuration(self.layout)

        def hardware_check() -> tuple[dict[str, Any], ...]:
            from dispenser_conditioning_mcp.domain import normalize_observation
            from dispenser_conditioning_mcp.hicube import HiCubeNeoPressureSource
            from dispenser_conditioning_mcp.siglent import DispenserPowerController

            source = HiCubeNeoPressureSource(
                client_file=configuration.hicube.client_file,
                host=configuration.hicube.host,
                port=configuration.hicube.port,
                timeout_s=configuration.hicube.timeout_s,
            )
            normalize_observation(source.read())
            DispenserPowerController(configuration.siglent).read_state()
            return (
                {"name": "pressure_source", "ok": True},
                {"name": "power_source", "ok": True},
            )

        return await asyncio.to_thread(hardware_check)

    async def _start_candidate(
        self, settings: OperatorConsoleSettings
    ) -> MCPServer[None]:
        if settings.backend == "simulation":
            from dispenser_conditioning_mcp.simulation_app import (
                create_simulation_server_from_config,
            )

            return create_simulation_server_from_config(
                settings.simulator_configuration(),
                layout=self.layout,
                learning=settings.learning,
            )
        from dispenser_conditioning_mcp.app import create_configured_server

        return create_configured_server(settings.hardware_configuration(self.layout))


class RuntimeMCPServer(MCPServer[None]):
    """Stable process endpoint that forwards only to the explicitly active run."""

    def __init__(self, coordinator: RuntimeCoordinator) -> None:
        super().__init__(
            "Dispenser Conditioning",
            instructions=(
                "Read server status before using a run. Instrument mutations require "
                "the exact active run_binding and are rejected while the human operator "
                "owns control. Stopping or starting a run never changes an output."
            ),
        )
        self.coordinator = coordinator

    async def list_tools(self) -> list[Tool]:
        tools = [
            Tool(
                name=READ_SERVER_STATUS_TOOL,
                description="Read public idle/run, acquisition, authority and registered-capability status without querying instruments.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                annotations=ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
            )
        ]
        selected = self.coordinator.selected
        if selected is None:
            saved = self.coordinator.saved_run_access()
            if saved is not None:
                tools.extend(saved.history.tools())
                tools.extend(
                    tool
                    for tool in LearningModeService.tools()
                    if tool.name in SAVED_LEARNING_AGENT_TOOLS
                )
            return tools
        for tool in await selected.server.list_tools():
            if tool.name == HIDDEN_OPERATOR_TOOL:
                continue
            if tool.name in AGENT_INSTRUMENT_MUTATIONS:
                schema = dict(tool.input_schema)
                properties = dict(cast(Mapping[str, Any], schema.get("properties", {})))
                properties["run_binding"] = {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": "Opaque active-run binding returned by read_server_status.",
                }
                required = list(cast(list[str], schema.get("required", [])))
                if "run_binding" not in required:
                    required.append("run_binding")
                schema.update(
                    {
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    }
                )
                tool = tool.model_copy(update={"input_schema": schema})
            tools.append(tool)
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        if name == READ_SERVER_STATUS_TOOL:
            if arguments:
                return error_result("Status request does not match the closed schema.")
            value = self.coordinator.public_status()
            return CallToolResult(
                content=[
                    TextContent(
                        type="text", text=json.dumps(value, separators=(",", ":"))
                    )
                ],
                structured_content=value,
            )
        if name == HIDDEN_OPERATOR_TOOL:
            return error_result("Not executed: run settings are operator-only.")
        return await self.coordinator.dispatch_agent(name, arguments, context)


class RuntimeDashboardApplication:
    """Resolve dashboard readers only after a run has been explicitly created."""

    def __init__(self, coordinator: RuntimeCoordinator) -> None:
        self.coordinator = coordinator
        self._binding: str | None = None
        self._application: Starlette | None = None

    def application(self) -> Starlette:
        active = self.coordinator.active
        selected = self.coordinator.selected
        directory: Path | None = None
        if selected is not None:
            recording = getattr(selected.server, "recording", None)
            candidate = getattr(recording, "directory", None)
            if isinstance(candidate, Path):
                directory = candidate
        if directory is None:
            directory = _latest_recorded_run(
                self.coordinator.layout.project_root / "runs"
            )
        binding = (
            active.run_binding
            if active
            else f"idle:{directory.name if directory is not None else 'empty'}"
        )
        if self._application is not None and binding == self._binding:
            return self._application
        routes: list[BaseRoute] = list(_console_routes(self.coordinator.access))
        if directory is not None:
            routes.extend(
                dashboard_routes(
                    directory,
                    replay=active is None,
                    access=self.coordinator.access,
                    run_root=self.coordinator.layout.project_root / "runs",
                )
            )
        else:
            routes.extend(_idle_dashboard_routes())
        self._binding = binding
        self._application = Starlette(routes=routes)
        return self._application

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.application()(scope, receive, send)


def _latest_recorded_run(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    for candidate in sorted(root.iterdir(), reverse=True):
        if (
            candidate.is_dir()
            and not candidate.is_symlink()
            and not candidate.is_junction()
            and (candidate / "metadata.json").is_file()
            and (candidate / "events.jsonl").is_file()
        ):
            return candidate
    return None


def _idle_dashboard_routes() -> list[Route]:
    async def dashboard(_request: Request) -> Response:
        return RedirectResponse("/console", status_code=303)

    async def unavailable(_request: Request) -> Response:
        return JSONResponse(
            {
                "error": "No run is active; start a checked run from the operator console."
            },
            status_code=409,
            headers={"Cache-Control": "no-store"},
        )

    return [
        Route("/", dashboard),
        Route("/dashboard", dashboard),
        Route("/api/session", unavailable),
        Route("/api/runs", unavailable),
    ]


def _console_routes(access: DashboardAccess) -> list[BaseRoute]:
    assets = Path(__file__).with_name("dashboard_assets")

    async def page(request: Request) -> Response:
        html = (assets / "console.html").read_text(encoding="utf-8")
        authorized = access.authorized(request)
        panel = (
            '<section class="banner" aria-label="Dashboard access phrase"><strong>Dashboard access phrase</strong><div id="dashboard-access-phrase" style="font-size:26px;letter-spacing:1px;margin:8px 0;user-select:all">'
            + escape(access.token)
            + "</div><small>Valid until server restart. Share only with authorized viewers.</small></section>"
            if authorized
            else '<p class="banner">Operator login is required to view or change settings. <a class="operator-unlock" href="/dashboard/login">Enter access phrase</a></p>'
        )
        return HTMLResponse(
            html.replace("<!-- DASHBOARD_OPERATOR_ACCESS -->", panel).replace(
                "<!-- OPERATOR_AUTHORIZED -->", "true" if authorized else "false"
            ),
            headers={"Cache-Control": "no-store"},
        )

    async def script(_request: Request) -> Response:
        return FileResponse(
            assets / "console.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def inline_login_script(_request: Request) -> Response:
        return FileResponse(
            assets / "inline-login.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    return [
        Route("/console", page),
        Route("/console.js", script),
        Route("/inline-login.js", inline_login_script),
        *access.routes(),
    ]


async def _json_body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ValueError("Expected a JSON request")
    body = await request.body()
    if len(body) > MAX_OPERATOR_BODY_BYTES:
        raise ValueError("Operator request is too large")
    value: object = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return cast(dict[str, Any], value)


def runtime_routes(coordinator: RuntimeCoordinator) -> list[BaseRoute]:
    async def runtime(_request: Request) -> Response:
        return JSONResponse(
            coordinator.public_status(), headers={"Cache-Control": "no-store"}
        )

    async def get_settings(_request: Request) -> Response:
        try:
            return JSONResponse(
                coordinator.operator_settings(), headers={"Cache-Control": "no-store"}
            )
        except ConfigurationError as error:
            return JSONResponse({"error": str(error)}, status_code=409)

    async def put_settings(request: Request) -> Response:
        try:
            update = OperatorSettingsUpdate.model_validate(await _json_body(request))
            return JSONResponse(await coordinator.save_settings(update))
        except ConfigurationError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except (ValidationError, ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Settings request does not match the closed schema."},
                status_code=400,
            )
        except OSError:
            return JSONResponse(
                {
                    "error": "Settings could not be persisted; inspect operator diagnostics."
                },
                status_code=500,
            )

    async def check(request: Request) -> Response:
        try:
            model = RevisionRequest.model_validate(await _json_body(request))
            return JSONResponse(await coordinator.check(model.revision))
        except ConfigurationError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except (ValidationError, ValueError, json.JSONDecodeError):
            return JSONResponse({"error": "Check request is invalid."}, status_code=400)
        except Exception:
            LOGGER.exception("Operator candidate connection check failed")
            return JSONResponse(
                {
                    "error": "Read-only connection check failed; inspect operator diagnostics.",
                    "ok": False,
                },
                status_code=409,
            )

    async def start(request: Request) -> Response:
        try:
            model = StartRunRequest.model_validate(await _json_body(request))
            return JSONResponse(await coordinator.start_run(model))
        except ConfigurationError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except (ValidationError, ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Run-start request is invalid."}, status_code=400
            )
        except Exception:
            LOGGER.exception("Checked run allocation failed")
            return JSONResponse(
                {"error": "Run could not be started; inspect operator diagnostics."},
                status_code=409,
            )

    async def update(request: Request) -> Response:
        try:
            model = UpdateRunRequest.model_validate(await _json_body(request))
            return JSONResponse(await coordinator.update_run(model))
        except ConfigurationError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except (ValidationError, ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Run update request is invalid."}, status_code=400
            )

    async def stop(request: Request) -> Response:
        try:
            model = StopRunRequest.model_validate(await _json_body(request))
            return JSONResponse(await coordinator.stop_run(model))
        except ConfigurationError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        except (ValidationError, ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Run-stop request is invalid."}, status_code=400
            )

    async def learning(request: Request) -> Response:
        try:
            values = {
                name: int(request.query_params.get(name, default))
                for name, default in (
                    ("review_after", "0"),
                    ("review_limit", "50"),
                    ("note_after", "0"),
                    ("note_limit", "50"),
                )
            }
            if (
                values["review_after"] < 0
                or values["note_after"] < 0
                or not 1 <= values["review_limit"] <= 50
                or not 1 <= values["note_limit"] <= 50
            ):
                raise ValueError("Invalid learning cursor")
            value = coordinator.learning_snapshot(
                run_key=request.query_params.get("run", ""),
                operator_authorized=coordinator.access.authorized(request),
                **values,
            )
            return JSONResponse(value, headers={"Cache-Control": "no-store"})
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    async def review(request: Request) -> Response:
        artifacts = coordinator.selected_artifacts()
        if artifacts is None:
            return JSONResponse({"error": "No run is available"}, status_code=409)
        try:
            model = ReadReviewsRequest.model_validate(
                {
                    "run_key": request.query_params.get("run", ""),
                    "review_id": request.query_params.get("review_id"),
                }
            )
            return JSONResponse(
                artifacts.reviews(model.run_key, review_id=model.review_id),
                headers={"Cache-Control": "no-store"},
            )
        except (ValidationError, ValueError, OSError, KeyError, json.JSONDecodeError):
            return JSONResponse({"error": "Review is unavailable"}, status_code=400)

    async def note(request: Request) -> Response:
        artifacts = coordinator.selected_artifacts()
        if artifacts is None:
            return JSONResponse({"error": "No run is available"}, status_code=409)
        try:
            model = HumanNoteRequest.model_validate(await _json_body(request))
            return JSONResponse(artifacts.append_note(model))
        except (ValidationError, ValueError, OSError, KeyError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Invalid human note request"}, status_code=400
            )

    async def manual(request: Request) -> Response:
        try:
            model = HumanManualOperation.model_validate(await _json_body(request))
            result = await coordinator.human_operation(model)
            return JSONResponse(
                {
                    "ok": not result.is_error,
                    "result": result.model_dump(mode="json", by_alias=True),
                },
                status_code=200 if not result.is_error else 409,
            )
        except ValidationError:
            return JSONResponse(
                {"error": "Manual operation does not match the closed request schema"},
                status_code=400,
            )
        except ConfigurationError as error:
            return JSONResponse({"error": str(error)}, status_code=409)

    return [
        Route("/api/runtime", runtime),
        Route("/api/operator/settings", coordinator.access.protect(get_settings)),
        Route(
            "/api/operator/settings",
            coordinator.access.protect(put_settings),
            methods=["PUT"],
        ),
        Route(
            "/api/operator/check", coordinator.access.protect(check), methods=["POST"]
        ),
        Route(
            "/api/operator/run/start",
            coordinator.access.protect(start),
            methods=["POST"],
        ),
        Route(
            "/api/operator/run", coordinator.access.protect(update), methods=["PATCH"]
        ),
        Route(
            "/api/operator/run/stop",
            coordinator.access.protect(stop),
            methods=["POST"],
        ),
        Route("/api/learning", learning),
        Route("/api/learning/review", review),
        Route("/api/learning/note", coordinator.access.protect(note), methods=["POST"]),
        Route(
            "/api/learning/manual-operation",
            coordinator.access.protect(manual),
            methods=["POST"],
        ),
    ]


def bootstrap_transport(
    layout: SourceLayout | None = None,
) -> McpTransportConfiguration:
    """Load only listener fields; missing instrument settings never block idle web."""

    layout = layout or SourceLayout.discover()
    path = layout.mcp_settings_file
    if not path.is_file():
        return McpTransportConfiguration()
    try:
        with path.open("rb") as stream:
            document: object = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(
            "MCP bootstrap settings are unreadable or invalid."
        ) from error
    values = cast(dict[str, object], document)
    remote = values.get("allow_remote_access", False)
    port = values.get("port", 8000)
    if type(remote) is not bool or type(port) is not int or not 1024 <= port <= 65535:
        raise ConfigurationError("Bootstrap allow_remote_access or port is invalid.")
    return McpTransportConfiguration(remote, port)


def create_runtime_http_app(
    coordinator: RuntimeCoordinator, configuration: McpTransportConfiguration
) -> Starlette:
    from contextlib import asynccontextmanager

    from dispenser_conditioning_mcp.transport import NativeClientOnlyMiddleware

    app = coordinator.mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=False,
        stateless_http=False,
        max_request_body_size=256 * 1024,
        transport_security=configuration.transport_security(),
    )
    app.add_middleware(NativeClientOnlyMiddleware)
    app.router.routes.extend(runtime_routes(coordinator))
    app.router.routes.append(Mount("/", app=cast(ASGIApp, coordinator.dashboard)))
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def runtime_lifespan(application: Starlette):
        async with original_lifespan(application):
            try:
                yield
            finally:
                await coordinator.shutdown_lifecycle()

    app.router.lifespan_context = runtime_lifespan
    return app
