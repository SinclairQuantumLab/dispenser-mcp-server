"""Incremental read-only session event projection for the local dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from dispenser_conditioning_mcp import run_directory
from dispenser_conditioning_mcp.dashboard_access import DashboardAccess
from dispenser_conditioning_mcp.session_records import projections
from dispenser_conditioning_mcp.simulation_observer import SimulationObserverReader


def dashboard_routes(
    directory: Path,
    *,
    observer_file: Path | None = None,
    replay: bool = False,
    access: DashboardAccess | None = None,
) -> list[BaseRoute]:
    access = access or DashboardAccess()
    directory = directory.resolve()
    readers: dict[Path, tuple[SessionTail, SimulationObserverReader]] = {}

    def selected(
        request: Request,
    ) -> tuple[SessionTail, SimulationObserverReader, bool]:
        name = request.query_params.get("run", "")
        path = directory
        if name:
            if name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError("Choose a run from the list")
            root = run_directory.RUNS_DIRECTORY.resolve()
            path = (root / name).resolve()
            if path.parent != root or not path.is_dir():
                raise ValueError("Run is not an immediate folder in the runs directory")
            reason = unavailable_reason(path)
            if reason:
                raise ValueError(reason)
        if path not in readers:
            readers[path] = (
                SessionTail(path),
                SimulationObserverReader(
                    path, observer_file=observer_file if path == directory else None
                ),
            )
        tail, observer = readers[path]
        return tail, observer, replay or path != directory

    async def run_list(request: Request) -> Response:
        items = [
            {
                "key": "",
                "name": directory.name,
                "label": "Configured saved run"
                if replay
                else "Live view · current process",
                "available": True,
                "reason": None,
            }
        ]
        root = run_directory.RUNS_DIRECTORY.resolve()
        try:
            children = sorted(root.iterdir(), key=lambda path: path.name, reverse=True)
        except FileNotFoundError:
            children = []
        except OSError as error:
            return JSONResponse({"error": str(error)}, status_code=500)
        for path in children:
            if (
                not path.is_dir()
                or path.resolve().parent != root
                or path.resolve() == directory
            ):
                continue
            reason = unavailable_reason(path)
            items.append(
                {
                    "key": path.name,
                    "name": path.name,
                    "label": path.name,
                    "available": reason is None,
                    "reason": reason,
                }
            )
        return JSONResponse(
            {"runs": items, "live_view_available": not replay},
            headers={"Cache-Control": "no-store"},
        )

    async def simulation_state(request: Request) -> Response:
        try:
            _, observer, _ = selected(request)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        return JSONResponse(observer.snapshot(), headers={"Cache-Control": "no-store"})

    assets = Path(__file__).with_name("dashboard_assets")

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
        snapshot = tail.snapshot(after, generation)
        snapshot["recording_view"] = "saved_recording" if saved else "process_session"
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})

    async def page(request: Request) -> Response:
        return FileResponse(
            assets / "session.html", headers={"Cache-Control": "no-store"}
        )

    async def script(request: Request) -> Response:
        return FileResponse(
            assets / "session.js",
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
        Route("/vendor/plotly-basic-4.0.0.min.js", plotly),
        Route("/api/runs", run_list),
        Route("/api/session", data),
        Route("/api/simulation-state", simulation_state),
    ]
    protected: list[BaseRoute] = [
        Route(route.path, access.protect(route.endpoint)) for route in routes
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


class SessionTail:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.offset = 0
        self.identity = None
        self.generation = 0
        self.events: list[dict[str, Any]] = []
        self.projected: list[list[tuple[str, dict[str, Any]]]] = []
        self.errors = 0
        self.last_error = None

    def snapshot(self, after: int = 0, generation: int = -1) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        status, message = "ready", None
        try:
            metadata = json.loads(
                (self.directory / "metadata.json").read_text(encoding="utf-8")
            )
            path = self.directory / "events.jsonl"
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity != self.identity or stat.st_size < self.offset:
                self.offset, self.events, self.projected = 0, [], []
                self.errors, self.last_error = 0, None
                self.generation += 1
                self.identity = identity
            with path.open("rb") as stream:
                stream.seek(self.offset)
                for _ in range(5000):
                    line = stream.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        message = "Waiting for the current event line to finish"
                        break
                    self.offset = stream.tell()
                    try:
                        event = json.loads(line)
                        if (
                            event["schema_version"] != 1
                            or event["session_id"] != metadata["session_id"]
                        ):
                            raise ValueError("Wrong schema or session ID")
                        rows = projections(event)
                        json.dumps(event, allow_nan=False)
                        self.events.append(event)
                        self.projected.append(rows)
                    except (ValueError, TypeError, KeyError, AttributeError) as error:
                        self.errors += 1
                        self.last_error = str(error)
        except FileNotFoundError:
            status, message = "waiting", "Waiting for session metadata and events.jsonl"
        except (OSError, ValueError) as error:
            status, message = "error", str(error)
        reset = generation != self.generation or after > len(self.events)
        start = 0 if reset else max(0, after)
        tables: dict[str, list[dict[str, Any]]] = {
            name: [] for name in ("observations", "controls", "decisions")
        }
        for group in self.projected[start:]:
            for name, row in group:
                tables[name].append(row)
        return {
            "metadata": metadata,
            "status": status,
            "message": message,
            "source": str(self.directory),
            "generation": self.generation,
            "cursor": len(self.events),
            "reset": reset,
            "errors": self.errors,
            "last_error": self.last_error,
            "events": self.events[start:],
            **tables,
        }
