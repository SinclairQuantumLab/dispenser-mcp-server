"""Strict MCP contract for pressure observation and bounded dispenser power."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, InputRequiredResult, Tool, ToolAnnotations
from pydantic import BeforeValidator, Field

from dispenser_conditioning_mcp.config import ConfigurationError
from dispenser_conditioning_mcp.current_limit import (
    RELOAD_CURRENT_LIMIT_TOOL,
    CurrentLimitReloadResult,
)
from dispenser_conditioning_mcp.current_policy import (
    MAX_CONFIGURABLE_LOAD_CURRENT_A,
    SPD_PARALLEL_CURRENT_MAX_A,
    effective_load_current_limit,
)
from dispenser_conditioning_mcp.domain import (
    PressureObservationError,
    PressureObservationSource,
    VacuumPressureObservation,
    normalize_observation,
)
from dispenser_conditioning_mcp.power_domain import (
    NO_LOAD_TEST_CONFIRMATION,
    PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION,
    DispenserPowerState,
    PowerAcceptanceContext,
    PowerActionResult,
    PowerControlError,
    PowerController,
)
from dispenser_conditioning_mcp.recording_service import (
    DECLARATION_TOOL,
    ActionContext,
    CompletionAssessment,
    DeclarationResult,
    RecordingService,
    error_result,
    mark_execution_started,
)
from dispenser_conditioning_mcp.run_history import HISTORY_TOOLS, RunHistory

READ_VACUUM_PRESSURE_TOOL = "read_vacuum_pressure"
READ_POWER_STATE_TOOL = "read_dispenser_power_state"
PREPARE_POWER_TOOL = "prepare_dispenser_power"
ENABLE_OUTPUT_TOOL = "enable_dispenser_output"
SET_CURRENT_TOOL = "set_dispenser_current"
SHUTDOWN_POWER_TOOL = "shutdown_dispenser_power"

BASE_TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    RELOAD_CURRENT_LIMIT_TOOL: frozenset(),
    READ_VACUUM_PRESSURE_TOOL: frozenset(),
    READ_POWER_STATE_TOOL: frozenset(),
    PREPARE_POWER_TOOL: frozenset({"action_context"}),
    SET_CURRENT_TOOL: frozenset(
        {"target_current_a", "expected_current_a", "action_context"}
    ),
    SHUTDOWN_POWER_TOOL: frozenset(),
    DECLARATION_TOOL: frozenset({"action_context", "completion"}),
}


def _strict_json_number(value: object) -> float:
    """Accept JSON integer/float values without coercing strings or booleans."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a JSON number")
    return float(value)


class DispenserConditioningMCPServer(MCPServer[None]):
    """Advertise closed schemas and reject unknown arguments before integration."""

    def __init__(
        self,
        name: str,
        *,
        instructions: str,
        tool_arguments: Mapping[str, frozenset[str]],
        recording: RecordingService,
    ) -> None:
        super().__init__(name, instructions=instructions)
        self._tool_arguments = dict(tool_arguments)
        self.recording = recording
        self.history = RunHistory(
            recording.directory,
            completion_recorded=lambda: recording.completion_recorded,
        )

    async def list_tools(self) -> list[Tool]:
        tools = await super().list_tools()
        return [
            self._strict_input_schema(tool) for tool in tools
        ] + self.history.tools()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        if name in HISTORY_TOOLS:
            return await self.history.call(name, arguments)
        allowed = self._tool_arguments.get(name)
        rejection = None
        if allowed is not None:
            unknown = set(arguments).difference(allowed)
            if name == SHUTDOWN_POWER_TOOL:
                unknown.discard("action_context")
            if unknown:
                rejection = f"Not executed: {name} received an unsupported argument."

        async def dispatch(
            tool_name: str, clean_arguments: dict[str, Any]
        ) -> CallToolResult:
            if tool_name != SHUTDOWN_POWER_TOOL and "action_context" in arguments:
                clean_arguments = {
                    **clean_arguments,
                    "action_context": arguments["action_context"],
                }
            try:
                result = await super(DispenserConditioningMCPServer, self).call_tool(
                    tool_name, clean_arguments, context
                )
            except ToolError as error:
                return error_result(str(error))
            if isinstance(result, InputRequiredResult):
                return error_result(
                    "Not executed: interactive input rounds are unsupported for this instrument interface."
                )
            return result

        return await self.recording.process_call(
            name, arguments, dispatch, rejection=rejection
        )

    def _strict_input_schema(self, tool: Tool) -> Tool:
        if tool.name not in self._tool_arguments:
            return tool
        input_schema = {**tool.input_schema, "additionalProperties": False}
        return tool.model_copy(update={"input_schema": input_schema})


def create_server(
    pressure_source: PressureObservationSource,
    power_controller: PowerController,
    *,
    recording: RecordingService | None = None,
    reload_current_limit: Callable[[], CurrentLimitReloadResult] | None = None,
    initial_max_load_current_A: float = 4.8,
) -> DispenserConditioningMCPServer:
    """Build the MCP server around explicitly injected integrations."""

    acceptance_context = power_controller.acceptance_context
    tool_arguments = dict(BASE_TOOL_ARGUMENTS)
    confirmation_argument = (
        "parallel_connection_confirmation"
        if acceptance_context == "production_dispenser"
        else "no_load_test_connection_confirmation"
    )
    tool_arguments[ENABLE_OUTPUT_TOOL] = frozenset(
        {confirmation_argument, "action_context"}
    )
    server = DispenserConditioningMCPServer(
        "Dispenser Conditioning",
        instructions=_server_instructions(acceptance_context)
        + f" Initial operator combined-load current cap: {initial_max_load_current_A:g} A (effective {effective_load_current_limit(initial_max_load_current_A):g} A; software maximum {MAX_CONFIGURABLE_LOAD_CURRENT_A:g} A; SPD parallel maximum {SPD_PARALLEL_CURRENT_MAX_A:g} A). Operator may edit max_load_current_A and reload_dispenser_current_limit applies only that field. Cached schemas retain absolute bounds; state/reload results report the current cap.",
        tool_arguments=tool_arguments,
        recording=recording or RecordingService(),
    )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def reload_dispenser_current_limit() -> CurrentLimitReloadResult:  # pyright: ignore[reportUnusedFunction]
        """Apply only operator max_load_current_A from canonical settings; no arguments, actuation, or time advance. Missing/invalid value preserves old cap."""
        if reload_current_limit is None:
            raise ToolError(
                "Current-limit reload is unavailable in this injected server."
            )
        try:
            result = reload_current_limit()
            mark_execution_started()
            return result
        except ConfigurationError as error:
            raise ToolError(
                "Current limit was not changed: operator max_load_current_A is missing/invalid or settings unreadable."
            ) from error

    @server.tool(
        title="Read vacuum pressure",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    def read_vacuum_pressure() -> VacuumPressureObservation:  # pyright: ignore[reportUnusedFunction]
        """Read one G1 total-pressure snapshot; never infer activation."""

        try:
            mark_execution_started()
            return normalize_observation(pressure_source.read())
        except PressureObservationError as error:
            raise ToolError(
                "Vacuum pressure is unavailable from the configured read-only "
                "HiCube Neo source. Ask the operator to check local diagnostics."
            ) from error

    @server.tool(
        title="Read dispenser power state",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    def read_dispenser_power_state() -> DispenserPowerState:  # pyright: ignore[reportUnusedFunction]
        """Read bound topology, identity, native measurements, and safety limits."""

        return _power_call(power_controller.read_state)

    @server.tool(
        title="Prepare dispenser power",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    def prepare_dispenser_power(action_context: ActionContext) -> PowerActionResult:  # pyright: ignore[reportUnusedFunction]
        """Overwrite state: output off, zero current, then fixed compliance voltage."""

        return _power_call(power_controller.prepare)

    _register_enable_tool(server, power_controller, acceptance_context)

    @server.tool(
        title="Set dispenser current",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def set_dispenser_current(  # pyright: ignore[reportUnusedFunction]
        action_context: ActionContext,
        target_current_a: Annotated[
            float,
            Field(
                ge=0,
                le=SPD_PARALLEL_CURRENT_MAX_A,
                description=(
                    "Absolute commanded load-current limit in amperes; never a "
                    "measured load current."
                ),
            ),
            BeforeValidator(_strict_json_number),
        ],
        expected_current_a: Annotated[
            float,
            Field(
                ge=0,
                le=SPD_PARALLEL_CURRENT_MAX_A,
                description=(
                    "Last observed commanded load-current limit for compare-and-set."
                ),
            ),
            BeforeValidator(_strict_json_number),
        ],
    ) -> PowerActionResult:
        """Compare-and-set an absolute current target; never blindly increment."""

        return _power_call(
            lambda: power_controller.set_current(
                target_current_a=target_current_a,
                expected_current_a=expected_current_a,
            )
        )

    @server.tool(
        title="Shutdown dispenser power",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    def shutdown_dispenser_power() -> PowerActionResult:  # pyright: ignore[reportUnusedFunction]
        """Energy-reducing overwrite: outputs off, then zero current; not an E-stop."""

        return _power_call(power_controller.shutdown)

    @server.tool(
        name=DECLARATION_TOOL,
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    def record_conditioning_decision(  # pyright: ignore[reportUnusedFunction]
        action_context: ActionContext,
        completion: CompletionAssessment | None = None,
    ) -> DeclarationResult:
        """Record stated judgment or completion without actuation; not an OFF or activation claim."""
        return DeclarationResult(action_context=action_context, completion=completion)

    return server


def _register_enable_tool(
    server: MCPServer[None],
    power_controller: PowerController,
    acceptance_context: PowerAcceptanceContext,
) -> None:
    annotations = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    )
    if acceptance_context == "production_dispenser":

        @server.tool(
            name=ENABLE_OUTPUT_TOOL,
            title="Enable dispenser output",
            annotations=annotations,
        )
        def enable_production_dispenser_output(  # pyright: ignore[reportUnusedFunction]
            action_context: ActionContext,
            parallel_connection_confirmation: Annotated[
                Literal["confirmed_parallel_ch1"],
                Field(
                    description=(
                        "Exact confirmation supplied only after the human operator "
                        "was asked immediately before this call and verified the "
                        "physical parallel CH1 dispenser wiring."
                    )
                ),
            ],
        ) -> PowerActionResult:
            """Enable only after fresh human confirmation of dispenser wiring."""

            if (
                parallel_connection_confirmation
                != PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION
            ):
                raise ToolError("Fresh human parallel-wiring confirmation is required.")
            return _power_call(
                lambda: power_controller.enable(
                    confirmation=PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION
                )
            )

        return

    @server.tool(
        name=ENABLE_OUTPUT_TOOL,
        title="Enable unloaded HIL output",
        annotations=annotations,
    )
    def enable_no_load_test_output(  # pyright: ignore[reportUnusedFunction]
        action_context: ActionContext,
        no_load_test_connection_confirmation: Annotated[
            Literal["confirmed_no_dispenser_or_unapproved_load_connected"],
            Field(
                description=(
                    "Exact confirmation supplied only after the human operator was "
                    "asked immediately before this call and verified that no "
                    "dispenser or unapproved load is connected. Operator-approved "
                    "metrology wiring, including a voltmeter, may be present."
                )
            ),
        ],
    ) -> PowerActionResult:
        """Enable after fresh confirmation of only approved no-load test wiring."""

        if no_load_test_connection_confirmation != NO_LOAD_TEST_CONFIRMATION:
            raise ToolError(
                "Fresh human no-load test connection confirmation is required."
            )
        return _power_call(
            lambda: power_controller.enable(confirmation=NO_LOAD_TEST_CONFIRMATION)
        )


def _server_instructions(acceptance_context: PowerAcceptanceContext) -> str:
    if acceptance_context == "production_dispenser":
        physical_instruction = (
            "Before enabling output, ask the human operator immediately before the "
            "call to verify that the physical dispenser wiring is in the approved "
            "parallel CH1 configuration. Pass confirmed_parallel_ch1 only after "
            "that reply."
        )
    else:
        physical_instruction = (
            "This server is startup-bound to an unloaded HIL acceptance context. "
            "Before enabling output, ask the human operator immediately before the "
            "call to verify that no dispenser or unapproved load is connected. "
            "Pass confirmed_no_dispenser_or_unapproved_load_connected only after "
            "the human confirms that no dispenser or unapproved load is connected; "
            "operator-approved metrology wiring, including a voltmeter, may be present. "
            "Never use this "
            "context for a connected dispenser. After every mutating power operation, "
            "a separate measured-current query must remain within the inclusive fixed "
            "-0.001 A to +0.001 A safe band or a process-local stop trips and rejects "
            "later mutations. The read-only power "
            "state exposes latch diagnostics. Reset is strictly out-of-band and is not "
            "available through MCP."
        )
    return (
        "Each normal prepare/enable/set call requires brief action_context with the "
        "session and observation IDs returned in result metadata/text. Supply your "
        "decision time, action/background, stated rationale, and claim-specific "
        "confidence; this is recorded judgment, not a safety authorization. Use "
        "record_conditioning_decision for a hold or finish without actuation. "
        "Completion is an agent assessment, separate from verified output-off. "
        "shutdown_dispenser_power needs no context. "
        f"{physical_instruction} Never infer external wiring or no-load state from "
        "instrument mode. Observe configured total vacuum pressure and control one "
        "operator-bound Siglent SPD3000 topology through deterministic safety "
        "checks. Connection, channel, topology, identity, acceptance context, and "
        "absolute ceilings are operator-bound and cannot be supplied by a "
        "tool call. Only the operator current cap can be reapplied from its canonical file. Pressure is total gauge pressure, not rubidium partial pressure, "
        "and does not verify dispenser activation. Power readbacks do not verify "
        "activation either. This draft is not an unattended conditioning "
        "orchestrator or physical interlock."
    )


def _power_call[T](operation: Callable[[], T]) -> T:
    try:
        mark_execution_started()
        return operation()
    except PowerControlError as error:
        raise ToolError(error.public_message) from error
