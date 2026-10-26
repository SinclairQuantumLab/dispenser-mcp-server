"""Direct Python simulator backend: one RecordingAdapter, no device imports."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, Tool, ToolAnnotations

from dispenser_conditioning_mcp.config import (
    ConfigurationError,
    LearningModeConfiguration,
    SourceLayout,
)
from dispenser_conditioning_mcp.current_policy import (
    MAX_CONFIGURABLE_LOAD_CURRENT_A,
    SPD_PARALLEL_CURRENT_MAX_A,
    effective_load_current_limit,
)
from dispenser_conditioning_mcp.learning_mode import (
    LEARNING_AGENT_TOOLS,
    LearningModeService,
    SimulationLearningBackend,
)
from dispenser_conditioning_mcp.recording_service import error_result
from dispenser_conditioning_mcp.run_history import HISTORY_TOOLS, RunHistory
from dispenser_simulator.model import HiddenSimulatorConfig
from dispenser_simulator.recording import RecordingAdapter, create_recording_service
from dispenser_simulator.server import build_runtime


class SimulationMCPServer(MCPServer[None]):
    def __init__(
        self,
        adapter: RecordingAdapter,
        learning: LearningModeService | None = None,
    ) -> None:
        learning = learning or LearningModeService.disabled(
            adapter.service,
            acceptance_context=cast(
                Literal["production_dispenser", "no_load_test"],
                adapter.router.simulator.config.acceptance_context,
            ),
        )
        super().__init__(
            "dispenser-conditioning-simulator",
            instructions=f"Initial operator combined-load current cap: {adapter.router.simulator.config.max_load_current_a:g} A (effective {effective_load_current_limit(adapter.router.simulator.config.max_load_current_a):g} A; software maximum {MAX_CONFIGURABLE_LOAD_CURRENT_A:g} A; SPD parallel maximum {SPD_PARALLEL_CURRENT_MAX_A:g} A). reload_dispenser_current_limit reapplies only operator max_load_current_A; readback/reload results give current cap. Synthetic conditioning instruments. Use public observations and submit action context for normal controls; model state is available only for explicit saved-run hindsight or after successfully recorded completion; disclosure cannot be undone."
            + learning.instruction_suffix(),
        )
        self.adapter = adapter
        self.recording = adapter.service
        self.learning = learning
        self.history = RunHistory(
            self.recording.directory,
            completion_recorded=lambda: self.recording.completion_recorded,
        )

    async def list_tools(self) -> list[Tool]:
        return (
            [
                Tool(
                    name=spec["name"],
                    description=spec["description"],
                    input_schema=spec["inputSchema"],
                    output_schema=spec.get("outputSchema"),
                    annotations=ToolAnnotations(**spec["annotations"]),
                )
                for spec in self.adapter.catalog
            ]
            + self.history.tools()
            + self.learning.tools()
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult:
        if name in HISTORY_TOOLS:
            return await self.history.call(name, arguments)
        if name in LEARNING_AGENT_TOOLS:
            return await self.learning.call_agent_tool(name, arguments)
        rejection = self.learning.agent_mutation_rejection(name)
        elapsed_s = arguments.get("elapsed_s", 0)
        if (
            rejection is None
            and self.learning.configuration.human_control_only
            and name in {"read_vacuum_pressure", "read_dispenser_power_state"}
            and type(elapsed_s) in (int, float)
            and cast(float, elapsed_s) > 0
        ):
            rejection = (
                "Not executed: learning mode uses real-wall sampling and does not "
                "allow public reads to advance synthetic time."
            )
        if rejection is not None:

            async def unused_dispatch(
                _name: str, _arguments: dict[str, Any]
            ) -> CallToolResult:
                return error_result("Not executed")

            return await self.recording.process_call(
                name, arguments, unused_dispatch, rejection=rejection
            )
        return await self.adapter.call(name, arguments)


def create_simulation_server(
    settings: object,
    *,
    layout: SourceLayout | None = None,
    max_load_current_A: float = 4.8,
    learning: object = None,
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
    service = create_recording_service({}, layout=layout)
    config = replace(config, observer_file=str(service.directory / "observer.jsonl"))
    router, _ = build_runtime(config)
    adapter = RecordingAdapter(router, service, layout=layout)
    learning_configuration = LearningModeConfiguration.from_value(learning)
    acceptance_context = cast(
        Literal["production_dispenser", "no_load_test"], config.acceptance_context
    )
    learning_service = LearningModeService(
        learning_configuration,
        service,
        SimulationLearningBackend(
            adapter,
            acceptance_context=acceptance_context,
            control_enabled=config.control_enabled,
        ),
        acceptance_context=acceptance_context,
        control_enabled=config.control_enabled,
    )
    return SimulationMCPServer(adapter, learning_service)


def create_simulation_server_from_config(
    config: HiddenSimulatorConfig,
    *,
    layout: SourceLayout | None = None,
    learning: LearningModeConfiguration | None = None,
) -> SimulationMCPServer:
    """Allocate one recorded simulation run from an already validated candidate."""

    service = create_recording_service({}, layout=layout)
    config = replace(config, observer_file=str(service.directory / "observer.jsonl"))
    router, _ = build_runtime(config)
    adapter = RecordingAdapter(router, service, layout=layout)
    learning_configuration = learning or LearningModeConfiguration()
    acceptance_context = cast(
        Literal["production_dispenser", "no_load_test"],
        config.acceptance_context,
    )
    learning_service = LearningModeService(
        learning_configuration,
        service,
        SimulationLearningBackend(
            adapter,
            acceptance_context=acceptance_context,
            control_enabled=config.control_enabled,
        ),
        acceptance_context=acceptance_context,
        control_enabled=config.control_enabled,
    )
    return SimulationMCPServer(adapter, learning_service)
