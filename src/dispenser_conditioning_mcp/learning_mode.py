"""Optional passive learning acquisition and authenticated human controls."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, cast
from uuid import uuid4

from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from dispenser_conditioning_mcp.config import LearningModeConfiguration
from dispenser_conditioning_mcp.dashboard_access import DashboardAccess
from dispenser_conditioning_mcp.domain import (
    PressureObservationError,
    PressureObservationSource,
    RawPressureObservation,
    normalize_observation,
)
from dispenser_conditioning_mcp.power_domain import (
    NO_LOAD_TEST_CONFIRMATION,
    PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION,
    DispenserPowerState,
    EnableConfirmation,
    PowerAcceptanceContext,
    PowerActionResult,
    PowerControlError,
    PowerController,
)
from dispenser_conditioning_mcp.recording_service import (
    ActionContext,
    ClaimConfidence,
    RecordingService,
    error_result,
    mark_execution_started,
)
from dispenser_conditioning_mcp.run_history import RunCatalog
from dispenser_conditioning_mcp.session_records import as_dict, content_state, utc_now

LOGGER = logging.getLogger(__name__)

READ_LEARNING_STATUS_TOOL = "read_learning_mode_status"
APPEND_RUN_REVIEW_TOOL = "append_conditioning_run_review"
READ_RUN_REVIEWS_TOOL = "read_conditioning_run_reviews"
READ_RUN_NOTES_TOOL = "read_conditioning_run_notes"
LEARNING_AGENT_TOOLS = {
    READ_LEARNING_STATUS_TOOL,
    APPEND_RUN_REVIEW_TOOL,
    READ_RUN_REVIEWS_TOOL,
    READ_RUN_NOTES_TOOL,
}
SAVED_LEARNING_AGENT_TOOLS = LEARNING_AGENT_TOOLS - {READ_LEARNING_STATUS_TOOL}
AGENT_INSTRUMENT_MUTATIONS = {
    "prepare_dispenser_power",
    "enable_dispenser_output",
    "set_dispenser_current",
    "shutdown_dispenser_power",
}
MAX_ARTIFACT_LINE_BYTES = 256 * 1024
MAX_GUIDANCE_CHANGES_BYTES = 128 * 1024


def _strict_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a JSON number")
    return float(value)


class SourceRange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    first_event_id: str = Field(min_length=1, max_length=128)
    last_event_id: str = Field(min_length=1, max_length=128)


class GuidanceChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision_ref: str = Field(min_length=1, max_length=256)
    before: str = Field(max_length=12000)
    after: str = Field(max_length=12000)
    reason: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=1000)
    source_ranges: Annotated[
        list[SourceRange], Field(default_factory=list, max_length=20)
    ]


class RunReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    run_key: str = Field(default="", max_length=255)
    review_id: str = Field(min_length=1, max_length=128)
    report_summary: str = Field(min_length=1, max_length=12000)
    change_summary: str = Field(min_length=1, max_length=4000)
    source_ranges: list[SourceRange] = Field(min_length=1, max_length=20)
    guidance_revision_refs: list[
        Annotated[str, Field(min_length=1, max_length=256)]
    ] = Field(default_factory=list, max_length=50)
    report_markdown: str | None = Field(default=None, max_length=65536)
    guidance_changes: Annotated[
        list[GuidanceChange], Field(default_factory=list, max_length=50)
    ]


class ReadReviewsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    run_key: str = Field(default="", max_length=255)
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=50)
    review_id: str | None = Field(default=None, min_length=1, max_length=128)


class ReadNotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    run_key: str = Field(default="", max_length=255)
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=50)


class HumanActionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: str = Field(min_length=1, max_length=500)
    reason_summary: str = Field(min_length=1, max_length=2000)


class HumanManualOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: str = Field(default="", max_length=255)
    run_binding: str | None = Field(default=None, min_length=1, max_length=256)
    operator_takeover_confirmation: Literal["confirmed_operator_takeover"] | None = None
    operation: Literal["prepare", "enable", "set_current", "shutdown"]
    action_context: HumanActionContext
    target_current_a: Annotated[float, BeforeValidator(_strict_number)] | None = None
    expected_current_a: Annotated[float, BeforeValidator(_strict_number)] | None = None
    parallel_connection_confirmation: str | None = None
    no_load_test_connection_confirmation: str | None = None


class HumanNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    run: str = Field(default="", max_length=255)
    note: str = Field(min_length=1, max_length=4000)


class LearningInstrumentBackend(Protocol):
    @property
    def acceptance_context(self) -> PowerAcceptanceContext:
        raise NotImplementedError

    @property
    def control_enabled(self) -> bool:
        raise NotImplementedError

    async def execute(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> CallToolResult:
        raise NotImplementedError


def _success_result(value: BaseModel | Mapping[str, Any]) -> CallToolResult:
    data = (
        value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    )
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(data, separators=(",", ":")))
        ],
        structured_content=data,
    )


class SerializedHardwareBackend(PressureObservationSource, PowerController):
    """One shared ownership lock for agent reads, sampler reads and human controls."""

    def __init__(
        self,
        pressure_source: PressureObservationSource,
        power_controller: PowerController,
        *,
        control_enabled: bool,
    ) -> None:
        self._pressure_source = pressure_source
        self._power_controller = power_controller
        self._operation_lock = threading.RLock()
        self.control_enabled = control_enabled

    @property
    def acceptance_context(self) -> PowerAcceptanceContext:
        return self._power_controller.acceptance_context

    def read(self) -> RawPressureObservation:
        with self._operation_lock:
            return self._pressure_source.read()

    def read_state(self) -> DispenserPowerState:
        with self._operation_lock:
            return self._power_controller.read_state()

    def prepare(self) -> PowerActionResult:
        with self._operation_lock:
            return self._power_controller.prepare()

    def enable(self, *, confirmation: EnableConfirmation) -> PowerActionResult:
        with self._operation_lock:
            return self._power_controller.enable(confirmation=confirmation)

    def set_current(
        self, *, target_current_a: float, expected_current_a: float
    ) -> PowerActionResult:
        with self._operation_lock:
            return self._power_controller.set_current(
                target_current_a=target_current_a,
                expected_current_a=expected_current_a,
            )

    def shutdown(self) -> PowerActionResult:
        with self._operation_lock:
            return self._power_controller.shutdown()

    async def execute(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> CallToolResult:
        return await asyncio.to_thread(self._execute_sync, name, dict(arguments or {}))

    def _execute_sync(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        try:
            mark_execution_started()
            if name == "read_vacuum_pressure":
                return _success_result(normalize_observation(self.read()))
            if name == "read_dispenser_power_state":
                return _success_result(self.read_state())
            if name == "prepare_dispenser_power":
                return _success_result(self.prepare())
            if name == "enable_dispenser_output":
                field = (
                    "parallel_connection_confirmation"
                    if self.acceptance_context == "production_dispenser"
                    else "no_load_test_connection_confirmation"
                )
                return _success_result(
                    self.enable(confirmation=cast(EnableConfirmation, arguments[field]))
                )
            if name == "set_dispenser_current":
                return _success_result(
                    self.set_current(
                        target_current_a=cast(float, arguments["target_current_a"]),
                        expected_current_a=cast(float, arguments["expected_current_a"]),
                    )
                )
            if name == "shutdown_dispenser_power":
                return _success_result(self.shutdown())
            return error_result("Unsupported learning backend operation.")
        except PressureObservationError:
            return error_result(
                "Vacuum pressure is unavailable; inspect operator diagnostics."
            )
        except PowerControlError as error:
            return error_result(error.public_message)
        except Exception:
            LOGGER.exception("Learning hardware operation failed")
            return error_result(
                "Instrument operation failed. Inspect operator diagnostics; do not blindly retry."
            )


class SimulationLearningBackend:
    """Serialize learning sampling and human controls with the simulator clock."""

    def __init__(
        self,
        adapter: Any,
        *,
        acceptance_context: PowerAcceptanceContext,
        control_enabled: bool,
    ) -> None:
        self.adapter = adapter
        self._acceptance_context: PowerAcceptanceContext = acceptance_context
        self._control_enabled = control_enabled

    @property
    def acceptance_context(self) -> PowerAcceptanceContext:
        return self._acceptance_context

    @property
    def control_enabled(self) -> bool:
        return self._control_enabled

    async def execute(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> CallToolResult:
        # No elapsed_s is supplied: the simulator advances only by actual wall time.
        return cast(
            CallToolResult,
            await self.adapter.execute_unrecorded(name, dict(arguments or {})),
        )


class LearningArtifactStore:
    """Bounded run-owned notes and reviews, separate from raw instrument events."""

    def __init__(self, current: Path) -> None:
        self.catalog = RunCatalog(current, root=current.parent)
        self._lock = threading.RLock()
        self._power_cache: dict[Path, tuple[int, int, dict[str, Any] | None]] = {}

    def resolve(self, run_key: str) -> Path:
        return self.catalog.resolve(run_key)

    def current(self, path: Path) -> bool:
        return self.catalog.protect_current and path.resolve() == self.catalog.current

    def mark_saved(self) -> None:
        """Treat the formerly live run as saved without altering its records."""

        self.catalog.protect_current = False

    @staticmethod
    def _records(path: Path, name: str) -> list[dict[str, Any]]:
        file = path / name
        if not file.exists():
            return []
        records: list[dict[str, Any]] = []
        with file.open("rb") as stream:
            for line in stream:
                if len(line) > MAX_ARTIFACT_LINE_BYTES or not line.endswith(b"\n"):
                    raise ValueError(
                        "Learning artifact record is incomplete or oversized"
                    )
                value: object = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Learning artifact record is invalid")
                records.append(cast(dict[str, Any], value))
        return records

    @staticmethod
    def _append(path: Path, name: str, record: dict[str, Any]) -> None:
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_ARTIFACT_LINE_BYTES:
            raise ValueError("Learning artifact record exceeds the fixed size limit")
        with (path / name).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
            stream.flush()

    @staticmethod
    def _event_ids(path: Path) -> set[str]:
        values: set[str] = set()
        with (path / "events.jsonl").open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                value: object = json.loads(line)
                if isinstance(value, dict):
                    record = cast(dict[str, Any], value)
                    event_id = record.get("event_id")
                    if isinstance(event_id, str):
                        values.add(event_id)
        return values

    def append_review(self, request: RunReviewRequest) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        encoded_changes = json.dumps(
            payload["guidance_changes"], ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(encoded_changes) > MAX_GUIDANCE_CHANGES_BYTES:
            raise ValueError("guidance_changes exceeds the fixed encoded-size limit")
        path = self.resolve(request.run_key)
        with self._lock:
            records = self._records(path, "learning-reviews.jsonl")
            for record in records:
                if record.get("review_id") != request.review_id:
                    continue
                prior = {key: record.get(key) for key in payload}
                if prior != payload:
                    raise ValueError(
                        "review_id is already attached to different content"
                    )
                return {**record, "idempotent_replay": True}
            source_ids = self._event_ids(path)
            required_ids = {
                item
                for source_range in request.source_ranges
                for item in (
                    source_range.first_event_id,
                    source_range.last_event_id,
                )
            }
            for change in request.guidance_changes:
                required_ids.update(
                    item
                    for source_range in change.source_ranges
                    for item in (
                        source_range.first_event_id,
                        source_range.last_event_id,
                    )
                )
            if not required_ids.issubset(source_ids):
                raise ValueError("Review source range references an unknown run event")
            metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            record = {
                **payload,
                "session_id": metadata["session_id"],
                "recorded_at": utc_now(),
                "idempotent_replay": False,
            }
            self._append(path, "learning-reviews.jsonl", record)
            return record

    @staticmethod
    def _review_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "review_id",
                "session_id",
                "recorded_at",
                "report_summary",
                "change_summary",
                "source_ranges",
                "guidance_revision_refs",
            )
        } | {
            "detail_available": bool(
                record.get("report_markdown") or record.get("guidance_changes")
            )
        }

    def reviews(
        self,
        run_key: str,
        *,
        after: int = 0,
        limit: int = 50,
        review_id: str | None = None,
    ) -> dict[str, Any]:
        path = self.resolve(run_key)
        with self._lock:
            records = self._records(path, "learning-reviews.jsonl")
        if review_id is not None:
            for record in records:
                if record.get("review_id") == review_id:
                    return {"review": record}
            raise ValueError("Review was not found in the selected run")
        end = min(after + limit, len(records))
        return {
            "items": [self._review_summary(item) for item in records[after:end]],
            "after": end,
            "has_more": end < len(records),
        }

    def append_note(self, request: HumanNoteRequest) -> dict[str, Any]:
        path = self.resolve(request.run)
        record = {
            "note_id": str(uuid4()),
            "session_id": json.loads(
                (path / "metadata.json").read_text(encoding="utf-8")
            )["session_id"],
            "recorded_at": utc_now(),
            "source": "human",
            "note": request.note,
        }
        with self._lock:
            self._append(path, "human-notes.jsonl", record)
        return record

    def notes(self, run_key: str, *, after: int, limit: int) -> dict[str, Any]:
        path = self.resolve(run_key)
        with self._lock:
            records = self._records(path, "human-notes.jsonl")
        end = min(after + limit, len(records))
        return {
            "items": records[after:end],
            "after": end,
            "has_more": end < len(records),
        }

    def latest_power_state(self, run_key: str) -> dict[str, Any] | None:
        path = self.resolve(run_key)
        events = path / "events.jsonl"
        with self._lock:
            stat = events.stat()
            cached = self._power_cache.get(path)
            if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2]
            latest: dict[str, Any] | None = None
            with events.open("rb") as stream:
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    event: object = json.loads(line)
                    if not isinstance(event, dict):
                        continue
                    state = content_state(
                        as_dict(as_dict(event).get("payload", {})).get("result", {})
                    )
                    if "native_current_setpoint_a" in state:
                        latest = state
            self._power_cache[path] = (stat.st_mtime_ns, stat.st_size, latest)
            return latest

    async def call_agent_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> CallToolResult:
        """Read or append bounded saved-run metadata without a live backend."""

        try:
            if name == APPEND_RUN_REVIEW_TOOL:
                data = self.append_review(RunReviewRequest.model_validate(arguments))
            elif name == READ_RUN_REVIEWS_TOOL:
                request = ReadReviewsRequest.model_validate(arguments)
                data = self.reviews(
                    request.run_key,
                    after=request.after,
                    limit=request.limit,
                    review_id=request.review_id,
                )
            elif name == READ_RUN_NOTES_TOOL:
                request = ReadNotesRequest.model_validate(arguments)
                data = self.notes(
                    request.run_key, after=request.after, limit=request.limit
                )
            else:
                raise ValueError("Unknown saved-run metadata tool")
            return _success_result(data)
        except ValidationError:
            return error_result(
                "Learning metadata request does not match the closed schema. No instrument action was performed."
            )
        except json.JSONDecodeError:
            return error_result(
                "Learning metadata is unavailable because a run artifact is invalid. No instrument action was performed."
            )
        except ValueError as error:
            return error_result(f"{error}. No instrument action was performed.")
        except (OSError, KeyError):
            return error_result(
                "Learning metadata is unavailable. Ask the operator to inspect local diagnostics. No instrument action was performed."
            )


class LearningModeService:
    """One optional passive sampler; never chooses or performs lifecycle actuation."""

    def __init__(
        self,
        configuration: LearningModeConfiguration,
        recording: RecordingService,
        backend: LearningInstrumentBackend | None,
        *,
        acceptance_context: PowerAcceptanceContext,
        control_enabled: bool,
    ) -> None:
        self.configuration = configuration
        self.recording = recording
        self.backend = backend
        self.acceptance_context = acceptance_context
        self.control_enabled = control_enabled
        self.artifacts = LearningArtifactStore(recording.directory)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._transaction_lock = asyncio.Lock()
        self.last_sample_at: str | None = None
        self.last_error: str | None = None
        self.latest_power_state: dict[str, Any] | None = None
        self._baseline: tuple[str, dict[str, Any]] | None = None

    @classmethod
    def disabled(
        cls,
        recording: RecordingService,
        *,
        acceptance_context: PowerAcceptanceContext,
    ) -> LearningModeService:
        return cls(
            LearningModeConfiguration(),
            recording,
            None,
            acceptance_context=acceptance_context,
            control_enabled=False,
        )

    @property
    def enabled(self) -> bool:
        return self.configuration.enabled

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def set_configuration(self, configuration: LearningModeConfiguration) -> None:
        """Replace sampler/authority policy only while the sampler is stopped."""

        if self.running:
            raise RuntimeError("Stop periodic acquisition before changing its policy")
        self.configuration = configuration

    def mark_saved(self) -> None:
        """Detach the stopped run from live-only artifact semantics."""

        self.artifacts.mark_saved()

    async def start(self) -> None:
        if not self.enabled or self.backend is None or self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="learning-mode-sampler")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.sample_once()
            except Exception:
                self.last_error = (
                    "Periodic acquisition failed; inspect operator diagnostics."
                )
                LOGGER.exception("Learning-mode periodic acquisition failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.configuration.sample_interval_s
                )
            except TimeoutError:
                pass

    async def sample_once(self) -> None:
        if not self.enabled or self.backend is None:
            return
        async with self._transaction_lock:
            errors: list[str] = []
            for name in ("read_vacuum_pressure", "read_dispenser_power_state"):
                result = await self.backend.execute(name)
                try:
                    self.recording.record_passive_observation(name, result)
                except Exception:
                    LOGGER.exception(
                        "Learning-mode passive observation recording failed"
                    )
                    errors.append("recording")
                if result.is_error:
                    errors.append(name)
                    continue
                state = dict(result.structured_content or {})
                if name == "read_dispenser_power_state":
                    await self._detect_external_change(state)
                    self.latest_power_state = state
            self.last_sample_at = utc_now()
            self.last_error = (
                "One or more periodic reads/records failed; inspect operator diagnostics."
                if errors
                else None
            )

    @staticmethod
    def _power_signature(state: Mapping[str, Any]) -> dict[str, Any]:
        fields = (
            "output_enabled",
            "commanded_load_current_limit_a",
            "native_current_setpoint_a",
            "native_voltage_setpoint_v",
            "live_operating_mode",
            "compliance_voltage_matches",
        )
        return {field: state.get(field) for field in fields}

    async def _detect_external_change(self, state: dict[str, Any]) -> None:
        detected_at = utc_now()
        if self._baseline is not None:
            prior_detected_at, prior_state = self._baseline
            previous = self._power_signature(prior_state)
            current = self._power_signature(state)
            changed = [key for key in previous if previous[key] != current[key]]
            if changed:
                self.recording.record_learning_event(
                    "external_change_detected",
                    {
                        "tool": "read_dispenser_power_state",
                        "source": "unknown",
                        "actor": "learning_sampler",
                        "intent_known": False,
                        "exact_change_time_known": False,
                        "observation_interval_started_at": prior_detected_at,
                        "detected_at": detected_at,
                        "previous_observed_at": prior_state.get("observed_at"),
                        "current_observed_at": state.get("observed_at"),
                        "changed_fields": changed,
                        "previous_values": {key: previous[key] for key in changed},
                        "detected_values": {key: current[key] for key in changed},
                    },
                )
        self._baseline = (detected_at, state)

    def note_known_power_state(self, state: Mapping[str, Any]) -> None:
        value = dict(state)
        self.latest_power_state = value
        self._baseline = (utc_now(), value)

    async def process_agent_mutation(
        self, operation: Callable[[], Awaitable[CallToolResult]]
    ) -> CallToolResult:
        """Serialize an agent write and publish only its successful known state."""

        async with self._transaction_lock:
            result = await operation()
            if not result.is_error:
                state = content_state(result.model_dump(mode="json", by_alias=True))
                if state:
                    self.note_known_power_state(state)
            return result

    def agent_mutation_rejection(self, name: str) -> str | None:
        if (
            self.enabled
            and self.configuration.human_control_only
            and name in AGENT_INSTRUMENT_MUTATIONS
        ):
            return (
                "Not executed: learning mode denies all agent instrument mutations, "
                "including shutdown. An authenticated human operator must use the "
                "dashboard control boundary."
            )
        return None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "sample_interval_s": self.configuration.sample_interval_s,
            "running": self.running,
            "last_sample_at": self.last_sample_at,
            "last_error": self.last_error,
            "acceptance_context": self.acceptance_context,
            "control_enabled": self.control_enabled,
            "permissions": {
                "agent_instrument_mutations": not (
                    self.enabled and self.configuration.human_control_only
                ),
                "agent_review_read": True,
                "agent_review_append": True,
                "agent_human_note_read": True,
                "human_instrument_controls": (
                    self.enabled and self.configuration.human_control_only
                ),
                "human_notes": True,
            },
        }

    def instruction_suffix(self) -> str:
        if not self.enabled:
            return (
                " Learning mode is disabled. Run reviews and human notes remain "
                "readable, and bounded run reviews may still be attached."
            )
        if not self.configuration.human_control_only:
            return (
                " Independent real-wall pressure/power sampling is enabled. Agent "
                "instrument mutations remain available through the ordinary safety "
                "contracts; authenticated human control first requires explicit "
                "operator takeover."
            )
        return (
            " Learning mode is enabled: periodic pressure/power acquisition runs "
            "independently on real wall time. Agent instrument mutations, including "
            "shutdown, are denied; authenticated human dashboard controls retain the "
            "same instrument controller safety rules. Agent run-review append/read and "
            "human-note read tools remain available."
        )

    @staticmethod
    def tools() -> list[Tool]:
        read_annotations = ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
        return [
            Tool(
                name=READ_LEARNING_STATUS_TOOL,
                description="Read learning-mode permissions and passive-sampler status without querying instruments.",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                annotations=read_annotations,
            ),
            Tool(
                name=APPEND_RUN_REVIEW_TOOL,
                description="Idempotently attach one bounded report and guidance-change record to a current or saved run; never controls instruments.",
                input_schema=RunReviewRequest.model_json_schema(),
                annotations=ToolAnnotations(
                    read_only_hint=False,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
            ),
            Tool(
                name=READ_RUN_REVIEWS_TOOL,
                description="Read bounded review summaries, or one full review by review_id, from a current or saved run without querying instruments.",
                input_schema=ReadReviewsRequest.model_json_schema(),
                annotations=read_annotations,
            ),
            Tool(
                name=READ_RUN_NOTES_TOOL,
                description="Read bounded human notes from a current or saved run without querying instruments.",
                input_schema=ReadNotesRequest.model_json_schema(),
                annotations=read_annotations,
            ),
        ]

    async def call_agent_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> CallToolResult:
        try:
            if name == READ_LEARNING_STATUS_TOOL:
                if arguments:
                    raise ValueError("Learning status accepts no arguments")
                data = self.status()
            else:
                return await self.artifacts.call_agent_tool(name, arguments)
            return _success_result(data)
        except ValidationError:
            return error_result(
                "Learning metadata request does not match the closed schema. No instrument action was performed."
            )
        except json.JSONDecodeError:
            return error_result(
                "Learning metadata is unavailable because a run artifact is invalid. No instrument action was performed."
            )
        except ValueError as error:
            return error_result(f"{error}. No instrument action was performed.")
        except (OSError, KeyError):
            return error_result(
                "Learning metadata is unavailable. Ask the operator to inspect local diagnostics. No instrument action was performed."
            )

    async def human_operation(self, request: HumanManualOperation) -> CallToolResult:
        if (
            not self.enabled
            or not self.configuration.human_control_only
            or self.backend is None
        ):
            return error_result(
                "Human-only authority is not active; explicit operator takeover is required."
            )
        if not self.control_enabled:
            return error_result(
                "Not executed: operator startup control authorization is disabled."
            )
        selected = self.artifacts.resolve(request.run)
        if not self.artifacts.current(selected):
            return error_result(
                "Not executed: manual controls are available only for the current run."
            )
        async with self._transaction_lock:
            arguments = self._manual_arguments(request)
            now = datetime.now(UTC)
            context = ActionContext.model_validate(
                {
                    "session_id": self.recording.session_id,
                    "decision_at": now.isoformat(),
                    "action": request.action_context.action,
                    "background": "Authenticated human dashboard operation in learning mode.",
                    "rationale_summary": request.action_context.reason_summary,
                    "observation_ids": [],
                    "confidence": ClaimConfidence(
                        claim="Human operator selected this manual operation.",
                        value=None,
                    ),
                }
            )
            name = {
                "prepare": "prepare_dispenser_power",
                "enable": "enable_dispenser_output",
                "set_current": "set_dispenser_current",
                "shutdown": "shutdown_dispenser_power",
            }[request.operation]
            try:
                self.recording.record_learning_event(
                    "human_action_context",
                    {
                        "tool": name,
                        "source": "human",
                        "actor": "human",
                        "action_context": request.action_context.model_dump(
                            mode="json"
                        ),
                        "received_at": utc_now(),
                    },
                )
            except Exception:
                LOGGER.exception("Human action-context recording failed")
                if request.operation != "shutdown":
                    return error_result(
                        "Not executed: human action context could not be recorded."
                    )

            async def dispatch(
                tool_name: str, clean_arguments: dict[str, Any]
            ) -> CallToolResult:
                return await cast(LearningInstrumentBackend, self.backend).execute(
                    tool_name, clean_arguments
                )

            result = await self.recording.process_call(
                name,
                {**arguments, "action_context": context.model_dump(mode="json")},
                dispatch,
                actor="human",
            )
            if not result.is_error:
                state = content_state(result.model_dump(mode="json", by_alias=True))
                if state:
                    self.note_known_power_state(state)
            return result

    def _manual_arguments(self, request: HumanManualOperation) -> dict[str, Any]:
        supplied = {
            "target_current_a": request.target_current_a,
            "expected_current_a": request.expected_current_a,
            "parallel_connection_confirmation": request.parallel_connection_confirmation,
            "no_load_test_connection_confirmation": request.no_load_test_connection_confirmation,
        }
        present = {key for key, value in supplied.items() if value is not None}
        if request.operation in {"prepare", "shutdown"}:
            expected: set[str] = set()
        elif request.operation == "set_current":
            expected = {"target_current_a", "expected_current_a"}
        elif self.acceptance_context == "production_dispenser":
            expected = {"parallel_connection_confirmation"}
            if (
                request.parallel_connection_confirmation
                != PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION
            ):
                raise ValueError("Fresh human parallel-wiring confirmation is required")
        else:
            expected = {"no_load_test_connection_confirmation"}
            if (
                request.no_load_test_connection_confirmation
                != NO_LOAD_TEST_CONFIRMATION
            ):
                raise ValueError("Fresh human no-load test confirmation is required")
        if present != expected:
            raise ValueError(
                "Manual operation fields do not match the selected operation"
            )
        return {key: value for key, value in supplied.items() if key in expected}

    def dashboard_snapshot(
        self,
        *,
        run_key: str,
        review_after: int,
        review_limit: int,
        note_after: int,
        note_limit: int,
        operator_authorized: bool,
    ) -> dict[str, Any]:
        path = self.artifacts.resolve(run_key)
        current = self.artifacts.current(path)
        power_state = (
            self.latest_power_state
            if current and self.latest_power_state is not None
            else self.artifacts.latest_power_state(run_key)
        )
        return {
            "enabled": self.enabled,
            "current": current,
            "operator_authorized": operator_authorized,
            "sample_interval_s": self.configuration.sample_interval_s,
            "running": self.running if current else False,
            "last_sample_at": self.last_sample_at if current else None,
            "last_error": self.last_error if current else None,
            "acceptance_context": self.acceptance_context,
            "power_state": power_state,
            "control_enabled": self.control_enabled,
            "human_notes": self.artifacts.notes(
                run_key, after=note_after, limit=note_limit
            ),
            "reviews": self.artifacts.reviews(
                run_key, after=review_after, limit=review_limit
            ),
        }


async def _json_body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise ValueError("Expected a JSON request")
    body = await request.body()
    if len(body) > MAX_ARTIFACT_LINE_BYTES:
        raise ValueError("Learning request is too large")
    value: object = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return cast(dict[str, Any], value)


def learning_routes(
    service: LearningModeService, access: DashboardAccess
) -> list[Route]:
    async def status(request: Request) -> Response:
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
            data = service.dashboard_snapshot(
                run_key=request.query_params.get("run", ""),
                operator_authorized=access.authorized(request),
                **values,
            )
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    async def detail(request: Request) -> Response:
        try:
            review_id = request.query_params.get("review_id")
            if not review_id:
                raise ValueError("review_id is required")
            data = service.artifacts.reviews(
                request.query_params.get("run", ""), review_id=review_id
            )
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    async def note(request: Request) -> Response:
        try:
            data = service.artifacts.append_note(
                HumanNoteRequest.model_validate(await _json_body(request))
            )
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
        except (ValidationError, ValueError, OSError, KeyError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Invalid human note request"}, status_code=400
            )

    async def manual(request: Request) -> Response:
        try:
            model = HumanManualOperation.model_validate(await _json_body(request))
            result = await service.human_operation(model)
            return JSONResponse(
                {
                    "ok": not result.is_error,
                    "result": result.model_dump(mode="json", by_alias=True),
                },
                status_code=200 if not result.is_error else 409,
                headers={"Cache-Control": "no-store"},
            )
        except ValidationError:
            return JSONResponse(
                {"error": "Manual operation does not match the closed request schema"},
                status_code=400,
            )
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    return [
        Route("/api/learning", status),
        Route("/api/learning/review", detail),
        Route("/api/learning/note", access.protect(note), methods=["POST"]),
        Route(
            "/api/learning/manual-operation",
            access.protect(manual),
            methods=["POST"],
        ),
    ]
