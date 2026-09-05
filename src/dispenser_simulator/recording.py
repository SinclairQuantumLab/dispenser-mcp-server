"""Source-checkout integration with the driver's independent recording service."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, TextContent

from dispenser_conditioning_mcp.recording_service import (
    RecordingService,
    mark_execution_started,
)
from dispenser_conditioning_mcp.run_directory import new_run_directory
from dispenser_conditioning_mcp.session_records import SessionRecorder

from .contract import recording_tool_specs
from .model import SimulationError, ToolRouter


def create_recording_service(environment: Mapping[str, str]) -> RecordingService:
    configured = environment.get("DISPENSER_SIM_SESSION_DIRECTORY")
    directory = Path(configured) if configured else new_run_directory("simulation")
    if not directory.is_absolute():
        raise ValueError("Simulator session directory must be absolute")
    recorder = SessionRecorder(
        directory,
        source=environment.get("DISPENSER_SIM_SESSION_SOURCE", "agent"),
        session_kind="simulated",
        label="Synthetic conditioning MCP session",
        observed_time_origin="2040-01-01T00:00:00Z",
    )
    return RecordingService(recorder)


class RecordingAdapter:
    def __init__(self, router: ToolRouter, service: RecordingService):
        self.router = router
        self.service = service
        self.catalog = recording_tool_specs(router.simulator.config.acceptance_context)
        self._schemas = {spec["name"]: spec["inputSchema"] for spec in self.catalog}
        observer = router.simulator.observer
        if observer.path is not None:
            observer_path = observer.path.resolve()
            session_path = service.directory.resolve()
            linked_path = (
                observer_path.relative_to(session_path).as_posix()
                if observer_path.is_relative_to(session_path)
                else str(observer_path)
            )
            link = {
                "session_id": service.session_id,
                "run_id": observer.run_id,
                "observer_file": linked_path,
            }
            try:
                with (service.directory / "observer-link.json").open(
                    "x", encoding="utf-8"
                ) as stream:
                    json.dump(link, stream)
            except OSError as error:
                print(
                    f"Simulator observer linkage could not be saved: {error}",
                    file=sys.stderr,
                )

    async def call(
        self, name: str, arguments: Mapping[str, Any] | None
    ) -> CallToolResult:
        args = dict(arguments or {})
        schema = self._schemas.get(name)
        rejection = None
        if schema is None:
            rejection = "Not executed: unknown tool name."
        elif set(args) - set(schema["properties"]) or set(
            schema.get("required", [])
        ) - set(args):
            rejection = (
                "Not executed: arguments do not match the closed public tool schema."
            )
        if rejection is None and "elapsed_s" in args:
            try:
                self.router.simulator.validate_elapsed(args["elapsed_s"])
            except SimulationError as error:
                rejection = f"Not executed: {error}"
        return await self.service.process_call(
            name,
            args,
            self._dispatch,
            rejection=rejection,
            request_virtual_time_s=self.router.simulator.state.virtual_time_s,
        )

    async def _dispatch(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        previous_timing = self.router.simulator.timing
        try:
            mark_execution_started()
            result = self.router.call(name, arguments)
        except SimulationError as exc:
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=str(exc))],
                _meta={"simulation_timing": self.router.simulator.timing}
                if self.router.simulator.timing is not previous_timing
                else None,
            )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(result, separators=(",", ":"), sort_keys=True),
                )
            ],
            structured_content=result,
        )
