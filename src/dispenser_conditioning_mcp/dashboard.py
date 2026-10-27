"""Incremental read-only session event projection for the local dashboard."""

from __future__ import annotations

import json
import math
import threading
from html import escape
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import BaseRoute, Route
from tzlocal import get_localzone_name

from dispenser_conditioning_mcp.dashboard_access import DashboardAccess
from dispenser_conditioning_mcp.session_records import (
    as_dict,
    content_state,
    error_text,
    projections,
    result_failed,
)
from dispenser_conditioning_mcp.simulation_observer import SimulationObserverReader

PAGE_SIZE = 200
DASHBOARD_PAGE_SIZE = 1000
MAX_DASHBOARD_PAGE_SIZE = 2000
OVERVIEW_BUCKETS = 128
MAX_OVERVIEW_MARKERS = 4096

_OVERVIEW_NUMERIC_FIELDS = (
    "pressure_mbar",
    "commanded_load_current_limit_a",
    "native_ch1_measured_current_a",
    "measured_ch2_current_a",
    "measured_parallel_load_current_a",
    "native_ch1_voltage_setpoint_v",
    "native_ch1_measured_voltage_v",
    "measured_ch2_voltage_v",
)


def server_time_display() -> dict[str, Any]:
    """Describe the current dashboard host zone without changing stored UTC."""
    try:
        name = get_localzone_name()
        if not name:
            raise ValueError("Local IANA time zone is unavailable")
        ZoneInfo(name)
    except (OSError, ValueError, ZoneInfoNotFoundError):
        return {
            "scope": "current_dashboard_host",
            "available": False,
            "iana_time_zone": None,
            "message": (
                "Server local time is unavailable because this host has no "
                "usable IANA time-zone configuration."
            ),
        }
    return {
        "scope": "current_dashboard_host",
        "available": True,
        "iana_time_zone": name,
        "message": (
            "Server local time uses the current dashboard host, not an inferred "
            "historical acquisition location."
        ),
    }


def dashboard_routes(
    directory: Path,
    *,
    observer_file: Path | None = None,
    replay: bool = False,
    access: DashboardAccess | None = None,
    run_root: Path | None = None,
) -> list[BaseRoute]:
    from dispenser_conditioning_mcp.run_history import RunCatalog

    access = access or DashboardAccess()
    server_display = server_time_display()
    catalog = RunCatalog(directory, protect_current=not replay, root=run_root)
    directory = directory.resolve()
    readers: dict[Path, tuple[SessionTail, SimulationObserverReader]] = {}

    def selected(
        request: Request,
    ) -> tuple[SessionTail, SimulationObserverReader, bool]:
        name = request.query_params.get("run", "")
        path = catalog.resolve(name)
        if path not in readers:
            reason = unavailable_reason(path) if name else None
            if reason:
                raise ValueError(reason)
            readers[path] = (
                SessionTail(path),
                SimulationObserverReader(
                    path, observer_file=observer_file if path == directory else None
                ),
            )
        tail, observer = readers[path]
        return tail, observer, replay or path != directory

    async def run_list(request: Request) -> Response:
        try:
            items = await run_in_threadpool(
                catalog.list, request.query_params.get("archived") == "true"
            )
            return JSONResponse(
                {"runs": items, "live_view_available": not replay},
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, OSError):
            return JSONResponse({"error": "Run list unavailable"}, status_code=500)

    async def manage_run(request: Request) -> Response:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse(
                {"error": "Expected dashboard JSON request"}, status_code=415
            )
        try:
            body = await request.body()
            if len(body) > 4096:
                raise ValueError("Run management request is too large")
            decoded: object = json.loads(body)
            if not isinstance(decoded, dict):
                raise ValueError("Expected a JSON object")
            values = cast(dict[str, Any], decoded)
            if set(values) - {
                "run",
                "display_name",
                "confirmation",
            }:
                raise ValueError("Unsupported run management fields")
            key = values.get("run", "")
            if not isinstance(key, str):
                raise ValueError("Invalid run key")
            path = catalog.resolve(key)
            result = catalog.manage(
                key,
                request.path_params["operation"],
                display_name=values.get("display_name"),
                confirmation=values.get("confirmation"),
            )
            if result.get("deleted"):
                readers.pop(path, None)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except json.JSONDecodeError:
            return JSONResponse(
                {"error": "Invalid run management JSON"}, status_code=400
            )
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except (OSError, TypeError):
            return JSONResponse(
                {"error": "Run files could not be updated"}, status_code=400
            )

    async def simulation_state(request: Request) -> Response:
        try:
            after = int(request.query_params.get("after", "0"))
            generation = int(request.query_params.get("generation", "-1"))
            _, observer, _ = selected(request)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        return JSONResponse(
            await run_in_threadpool(observer.snapshot, after, generation),
            headers={"Cache-Control": "no-store"},
        )

    assets = Path(__file__).with_name("dashboard_assets")

    async def decorate(
        request: Request,
        tail: SessionTail,
        saved: bool,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        management = await run_in_threadpool(catalog.management, tail.directory)
        snapshot["metadata"]["label"] = management["display_name"]
        snapshot["run_management"] = {
            **management,
            "name": tail.directory.name,
            "current": not replay and tail.directory == directory,
        }
        snapshot["operator_authorized"] = access.authorized(request)
        snapshot["server_time"] = dict(server_display)
        snapshot["recording_view"] = (
            "saved_recording" if saved else "process_session"
        )
        return snapshot

    async def data(request: Request) -> Response:
        try:
            after = int(request.query_params.get("after", "0"))
            generation = int(request.query_params.get("generation", "-1"))
        except ValueError:
            return JSONResponse({"error": "Invalid cursor"}, status_code=400)
        try:
            tail, _, saved = selected(request)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        snapshot = await run_in_threadpool(tail.snapshot, after, generation)
        snapshot = await decorate(request, tail, saved, snapshot)
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    async def overview(request: Request) -> Response:
        try:
            tail, _, saved = selected(request)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        snapshot = await run_in_threadpool(tail.overview)
        snapshot = await decorate(request, tail, saved, snapshot)
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    async def session_page(request: Request) -> Response:
        try:
            start = int(request.query_params.get("start", "0"))
            limit = int(
                request.query_params.get("limit", str(DASHBOARD_PAGE_SIZE))
            )
            generation = int(request.query_params["generation"])
            if start < 0 or not 1 <= limit <= MAX_DASHBOARD_PAGE_SIZE:
                raise ValueError
        except (KeyError, ValueError):
            return JSONResponse({"error": "Invalid page request"}, status_code=400)
        try:
            tail, _, saved = selected(request)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        snapshot = await run_in_threadpool(
            tail.page, start, generation, limit, True
        )
        snapshot = await decorate(request, tail, saved, snapshot)
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    async def session_record(request: Request) -> Response:
        event_id = request.query_params.get("event_id", "")
        try:
            generation = int(request.query_params["generation"])
            if not event_id or len(event_id) > 256:
                raise ValueError
        except (KeyError, ValueError):
            return JSONResponse({"error": "Invalid record request"}, status_code=400)
        try:
            tail, _, saved = selected(request)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        snapshot = await run_in_threadpool(tail.record, event_id, generation)
        snapshot = await decorate(request, tail, saved, snapshot)
        status_code = 409 if snapshot["reset"] else 200
        if snapshot["event"] is None and not snapshot["reset"]:
            status_code = 404
        return JSONResponse(
            snapshot,
            status_code=status_code,
            headers={"Cache-Control": "no-store"},
        )

    async def page(request: Request) -> Response:
        # Ordinary plots are public; only authorized viewers receive the phrase.
        html = (assets / "session.html").read_text(encoding="utf-8")
        authorized = access.authorized(request)
        panel = (
            '<section class="banner" aria-label="Dashboard access phrase"><strong>Dashboard access phrase</strong>'
            '<div id="dashboard-access-phrase" style="font-size:26px;letter-spacing:1px;margin:8px 0;user-select:all">'
            + escape(access.token)
            + "</div><small>Valid until server restart. Share only with authorized viewers.</small></section>"
            if authorized
            else '<p class="banner">Public observations and recorded decisions. <a class="operator-unlock" href="/dashboard/login">Unlock internal state and run management</a></p>'
        )
        return HTMLResponse(
            html.replace("<!-- DASHBOARD_OPERATOR_ACCESS -->", panel).replace(
                "<!-- OPERATOR_AUTHORIZED -->", "true" if authorized else "false"
            ),
            headers={"Cache-Control": "no-store"},
        )

    async def script(request: Request) -> Response:
        return FileResponse(
            assets / "session.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def inline_login_script(request: Request) -> Response:
        return FileResponse(
            assets / "inline-login.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def learning_script(request: Request) -> Response:
        return FileResponse(
            assets / "learning.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def plotly(request: Request) -> Response:
        return FileResponse(
            assets / "vendor" / "plotly-basic-4.0.0.min.js",
            media_type="text/javascript",
        )

    async def index(request: Request) -> Response:
        return RedirectResponse("/dashboard")

    routes = [
        Route("/", index),
        Route("/dashboard", page),
        Route("/session.js", script),
        Route("/inline-login.js", inline_login_script),
        Route("/learning.js", learning_script),
        Route("/vendor/plotly-basic-4.0.0.min.js", plotly),
        Route("/api/runs", run_list),
        Route("/api/runs/{operation}", manage_run, methods=["POST"]),
        Route("/api/session/overview", overview),
        Route("/api/session/page", session_page),
        Route("/api/session/record", session_record),
        Route("/api/session", data),
        Route("/api/simulation-state", simulation_state),
    ]
    protected: list[BaseRoute] = [
        Route(
            route.path,
            access.protect(route.endpoint)
            if route.path in {"/api/simulation-state", "/api/runs/{operation}"}
            else route.endpoint,
            methods=route.methods,
        )
        for route in routes
    ]
    return protected + access.routes()


def unavailable_reason(directory: Path) -> str | None:
    if (
        not (directory / "metadata.json").is_file()
        or not (directory / "events.jsonl").is_file()
    ):
        return (
            "Legacy or incomplete folder: metadata.json and events.jsonl are required"
        )
    try:
        metadata: object = json.loads(
            (directory / "metadata.json").read_text(encoding="utf-8")
        )
        if not isinstance(metadata, dict) or not cast(dict[str, Any], metadata).get(
            "session_id"
        ):
            return "Unsupported session metadata"
    except (OSError, ValueError):
        return "Session metadata cannot be read"
    return None


def dashboard_record(event: dict[str, Any]) -> dict[str, Any]:
    """One display record, not the raw MCP envelope or duplicated CSV tables."""
    view = {key: value for key, value in event.items() if key != "payload"}
    payload = as_dict(event.get("payload"))
    result = as_dict(payload.get("result"))
    if event.get("kind") == "external_change_detected":
        view.update(
            {
                "actor": payload.get("actor", "learning_sampler"),
                "tool": payload.get("tool"),
                "change_source": payload.get("source"),
                "intent_known": payload.get("intent_known"),
                "exact_change_time_known": payload.get("exact_change_time_known"),
                "observation_interval_started_at": payload.get(
                    "observation_interval_started_at"
                ),
                "detected_at": payload.get("detected_at"),
                "previous_observed_at": payload.get("previous_observed_at"),
                "current_observed_at": payload.get("current_observed_at"),
                "changed_fields": payload.get("changed_fields"),
                "previous_values": payload.get("previous_values"),
                "detected_values": payload.get("detected_values"),
            }
        )
    for _, row in projections(event):
        view.update(row)
    view.pop("arguments_json", None)
    basis = view.pop("basis_event_ids_json", None)
    if basis is not None:
        view["basis_event_ids"] = json.loads(basis)
    view["tool"] = payload.get("tool")
    view["execution"] = payload.get("execution") or as_dict(
        as_dict(result.get("_meta")).get("dispenser_conditioning")
    ).get("execution")
    view["is_error"] = event["kind"] == "call_error" or result_failed(result)
    view["error"] = payload.get("error_message") or (
        error_text(result) if view["is_error"] else None
    )
    arguments = {
        key: value
        for key, value in as_dict(payload.get("arguments")).items()
        if key not in {"action_context", "completion"}
    }
    if payload.get("tool") == "reload_dispenser_current_limit":
        state = content_state(result)
        for key in (
            "previous_max_load_current_A",
            "applied_max_load_current_A",
            "effective_max_load_current_A",
            "hardware_changed",
            "fresh_state_inspection_recommended",
            "notice",
        ):
            view[key] = state.get(key)
    if arguments:
        view["requested_arguments"] = arguments
    timing = as_dict(as_dict(result.get("_meta")).get("simulation_timing"))
    if timing:
        view["result_virtual_time_s"] = timing.get("virtual_time_s")
    return {key: value for key, value in view.items() if value is not None}


class SessionTail:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.offset = 0
        self.identity: tuple[int, int] | None = None
        self.session_id: str | None = None
        self.generation = 0
        self.events: list[dict[str, Any]] = []
        self.events_by_id: dict[str, dict[str, Any]] = {}
        self.errors = 0
        self.last_error: str | None = None
        self.metadata: dict[str, Any] = {}
        self.status = "waiting"
        self.message: str | None = None
        self._lock = threading.RLock()
        self._overview_cache: dict[str, Any] | None = None

    def _reset_locked(
        self, identity: tuple[int, int], session_id: str
    ) -> None:
        self.offset = 0
        self.events = []
        self.events_by_id = {}
        self.errors = 0
        self.last_error = None
        self.generation += 1
        self.identity = identity
        self.session_id = session_id
        self._overview_cache = None

    def _refresh_locked(self) -> None:
        self.status, self.message = "ready", None
        try:
            metadata: object = json.loads(
                (self.directory / "metadata.json").read_text(encoding="utf-8")
            )
            if not isinstance(metadata, dict):
                raise ValueError("Unsupported session metadata")
            self.metadata = cast(dict[str, Any], metadata)
            session_id = self.metadata.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise ValueError("Unsupported session metadata")
            path = self.directory / "events.jsonl"
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if (
                identity != self.identity
                or stat.st_size < self.offset
                or session_id != self.session_id
            ):
                self._reset_locked(identity, session_id)
            changed = False
            with path.open("rb") as stream:
                stream.seek(self.offset)
                while stream.tell() < stat.st_size:
                    line = stream.readline(stat.st_size - stream.tell())
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        self.message = "Waiting for the current event line to finish"
                        break
                    self.offset = stream.tell()
                    try:
                        event: object = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError("Expected an event object")
                        typed = cast(dict[str, Any], event)
                        if typed["session_id"] != session_id:
                            raise ValueError("Wrong session ID")
                        json.dumps(typed, allow_nan=False)
                        record = dashboard_record(typed)
                        record["record_number"] = len(self.events) + 1
                        self.events.append(record)
                        event_id = record.get("event_id")
                        if isinstance(event_id, str):
                            self.events_by_id[event_id] = record
                        changed = True
                    except (ValueError, TypeError, KeyError, AttributeError) as error:
                        self.errors += 1
                        self.last_error = str(error)
            if changed:
                self._overview_cache = None
        except FileNotFoundError:
            self.status = "waiting"
            self.message = "Waiting for session metadata and events.jsonl"
        except (OSError, ValueError) as error:
            self.status = "error"
            self.message = str(error)

    def _base_locked(self) -> dict[str, Any]:
        return {
            "metadata": dict(self.metadata),
            "status": self.status,
            "message": self.message,
            "source": str(self.directory),
            "generation": self.generation,
            "total_records": len(self.events),
            "errors": self.errors,
            "last_error": self.last_error,
        }

    def _overview_locked(self) -> dict[str, Any]:
        if self._overview_cache is not None:
            return {
                **self._overview_cache,
                "events": [dict(event) for event in self._overview_cache["events"]],
            }
        observations = [
            (index, event)
            for index, event in enumerate(self.events)
            if event.get("observation_kind") is not None
        ]
        selected: set[int] = set()
        bucket_count = min(OVERVIEW_BUCKETS, len(observations))
        if bucket_count:
            for bucket in range(bucket_count):
                start = bucket * len(observations) // bucket_count
                end = (bucket + 1) * len(observations) // bucket_count
                rows = observations[start:end]
                if not rows:
                    continue
                selected.update((rows[0][0], rows[-1][0]))
                for field in _OVERVIEW_NUMERIC_FIELDS:
                    numeric = [
                        (index, float(value))
                        for index, event in rows
                        if not isinstance((value := event.get(field)), bool)
                        and isinstance(value, (int, float))
                        and math.isfinite(value)
                    ]
                    if numeric:
                        selected.add(min(numeric, key=lambda row: row[1])[0])
                        selected.add(max(numeric, key=lambda row: row[1])[0])
        sample_indexes = set(selected)
        marker_indexes = [
            index
            for index, event in enumerate(self.events)
            if event.get("phase") is not None
            or event.get("kind")
            in {
                "decision",
                "call_error",
                "external_change_detected",
                "human_action_context",
            }
            or event.get("is_error") is True
        ]
        marker_half = MAX_OVERVIEW_MARKERS // 2
        returned_markers = (
            marker_indexes
            if len(marker_indexes) <= MAX_OVERVIEW_MARKERS
            else marker_indexes[:marker_half] + marker_indexes[-marker_half:]
        )
        selected.update(returned_markers)
        overview: dict[str, Any] = {
            "events": [dict(self.events[index]) for index in sorted(selected)],
            "total_observations": len(observations),
            "sample_count": len(sample_indexes),
            "marker_count": len(marker_indexes),
            "marker_count_returned": len(returned_markers),
            "markers_complete": len(returned_markers) == len(marker_indexes),
            "bucket_count": bucket_count,
            "bucket_basis": "observation_record_order",
        }
        self._overview_cache = overview
        return {**overview, "events": [dict(event) for event in overview["events"]]}

    def overview(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            return {**self._base_locked(), "overview": self._overview_locked()}

    def page(
        self,
        start: int,
        generation: int,
        limit: int = PAGE_SIZE,
        empty_on_reset: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            reset = generation != self.generation or start > len(self.events)
            page_start = 0 if reset else max(0, start)
            if reset and empty_on_reset:
                page_end = page_start
            else:
                page_end = min(page_start + limit, len(self.events))
            return {
                **self._base_locked(),
                "start": page_start,
                "end": page_end,
                "cursor": page_end,
                "has_more": page_end < len(self.events),
                "reset": reset,
                "events": [
                    dict(event) for event in self.events[page_start:page_end]
                ],
            }

    def record(self, event_id: str, generation: int) -> dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            reset = generation != self.generation
            record = None if reset else self.events_by_id.get(event_id)
            return {
                **self._base_locked(),
                "reset": reset,
                "event": dict(record) if record is not None else None,
            }

    def snapshot(self, after: int = 0, generation: int = -1) -> dict[str, Any]:
        return self.page(after, generation, PAGE_SIZE)
