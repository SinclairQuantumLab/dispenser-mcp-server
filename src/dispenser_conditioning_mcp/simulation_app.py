"""Direct Python simulator backend: one RecordingAdapter, no device imports."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, Tool, ToolAnnotations

from dispenser_conditioning_mcp.config import ConfigurationError, SourceLayout
from dispenser_simulator.model import HiddenSimulatorConfig
from dispenser_simulator.recording import RecordingAdapter, create_recording_service
from dispenser_simulator.server import build_runtime


class SimulationMCPServer(MCPServer[None]):
    def __init__(self, adapter: RecordingAdapter) -> None:
        super().__init__(
            "dispenser-conditioning-simulator",
            instructions=f"Initial operator combined-load current cap: {adapter.router.simulator.config.max_load_current_a:g} A (absolute 4.8 A). reload_dispenser_current_limit reapplies only operator max_load_current_A; readback/reload results give current cap. Synthetic conditioning instruments. Use public observations and submit action context for normal controls; no model-internal state is available through tools.",
        )
        self.adapter = adapter
        self.recording = adapter.service

    async def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=spec["name"],
                description=spec["description"],
                input_schema=spec["inputSchema"],
                output_schema=spec.get("outputSchema"),
                annotations=ToolAnnotations(**spec["annotations"]),
            )
            for spec in self.adapter.catalog
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult:
        return await self.adapter.call(name, arguments)


def create_simulation_server(
    settings: object,
    *,
    layout: SourceLayout | None = None,
    max_load_current_A: float = 4.8,
) -> SimulationMCPServer:
    if not isinstance(settings, dict):
        raise ConfigurationError("simulation must be a TOML table")
    settings = cast(dict[str, Any], settings)
    allowed = {"seed", "scenario", "control_enabled", "compliance_voltage_v"}
    if set(settings) - allowed:
        raise ConfigurationError("Unsupported simulation startup setting")
    seed, scenario = settings.get("seed"), settings.get("scenario")
    enabled, voltage = (
        settings.get("control_enabled", True),
        settings.get("compliance_voltage_v", 1.0),
    )
    if (
        not isinstance(seed, str)
        or not seed
        or not isinstance(scenario, str)
        or not scenario
        or type(enabled) is not bool
        or type(voltage) not in (int, float)
    ):
        raise ConfigurationError(
            "Simulation requires operator seed/scenario and valid test policy"
        )
    try:
        config = HiddenSimulatorConfig(
            seed=seed,
            scenario=scenario,
            control_enabled=enabled,
            compliance_voltage_v=float(voltage),
            max_load_current_a=max_load_current_A,
        )
    except ValueError:
        raise ConfigurationError("Invalid simulation startup policy") from None
    service = create_recording_service({})
    config = replace(config, observer_file=str(service.directory / "observer.jsonl"))
    router, _ = build_runtime(config)
    return SimulationMCPServer(RecordingAdapter(router, service, layout=layout))
