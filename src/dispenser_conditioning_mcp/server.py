"""Strict MCP contract for pressure observation and bounded dispenser power."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, InputRequiredResult, Tool, ToolAnnotations
from pydantic import BeforeValidator, Field

from dispenser_conditioning_mcp.domain import (
    PressureObservationError,
    PressureObservationSource,
    VacuumPressureObservation,
    normalize_observation,
)
from dispenser_conditioning_mcp.power_domain import (
    PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION,
    UNLOADED_HIL_CONFIRMATION,
    DispenserPowerState,
    PowerAcceptanceContext,
    PowerActionResult,
    PowerControlError,
    PowerController,
)

READ_VACUUM_PRESSURE_TOOL = "read_vacuum_pressure"
READ_POWER_STATE_TOOL = "read_dispenser_power_state"
PREPARE_POWER_TOOL = "prepare_dispenser_power"
ENABLE_OUTPUT_TOOL = "enable_dispenser_output"
SET_CURRENT_TOOL = "set_dispenser_current"
SHUTDOWN_POWER_TOOL = "shutdown_dispenser_power"

BASE_TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    READ_VACUUM_PRESSURE_TOOL: frozenset(),
    READ_POWER_STATE_TOOL: frozenset(),
    PREPARE_POWER_TOOL: frozenset(),
    SET_CURRENT_TOOL: frozenset({"target_current_a", "expected_current_a"}),
    SHUTDOWN_POWER_TOOL: frozenset(),
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
    ) -> None:
        super().__init__(name, instructions=instructions)
        self._tool_arguments = dict(tool_arguments)

    async def list_tools(self) -> list[Tool]:
        tools = await super().list_tools()
        return [self._strict_input_schema(tool) for tool in tools]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        allowed = self._tool_arguments.get(name)
        if allowed is not None:
            unknown = set(arguments).difference(allowed)
            if unknown:
                raise ToolError(f"{name} received an unsupported argument.")
        return await super().call_tool(name, arguments, context)

    def _strict_input_schema(self, tool: Tool) -> Tool:
        if tool.name not in self._tool_arguments:
            return tool
        input_schema = {**tool.input_schema, "additionalProperties": False}
        return tool.model_copy(update={"input_schema": input_schema})


def create_server(
    pressure_source: PressureObservationSource,
    power_controller: PowerController,
) -> MCPServer[None]:
    """Build the MCP server around explicitly injected integrations."""

    acceptance_context = power_controller.acceptance_context
    tool_arguments = dict(BASE_TOOL_ARGUMENTS)
    confirmation_argument = (
        "parallel_connection_confirmation"
        if acceptance_context == "production_dispenser"
        else "unloaded_hil_connection_confirmation"
    )
    tool_arguments[ENABLE_OUTPUT_TOOL] = frozenset({confirmation_argument})
    server: MCPServer[None] = DispenserConditioningMCPServer(
        "Dispenser Conditioning",
        instructions=_server_instructions(acceptance_context),
        tool_arguments=tool_arguments,
    )

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
    def prepare_dispenser_power() -> PowerActionResult:  # pyright: ignore[reportUnusedFunction]
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
        target_current_a: Annotated[
            float,
            Field(
                ge=0,
                le=4.8,
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
                le=6.4,
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
    def enable_unloaded_hil_output(  # pyright: ignore[reportUnusedFunction]
        unloaded_hil_connection_confirmation: Annotated[
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
        """Enable after fresh confirmation of only approved unloaded-HIL wiring."""

        if unloaded_hil_connection_confirmation != UNLOADED_HIL_CONFIRMATION:
            raise ToolError(
                "Fresh human unloaded-HIL connection confirmation is required."
            )
        return _power_call(
            lambda: power_controller.enable(confirmation=UNLOADED_HIL_CONFIRMATION)
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
            "-0.001 A to +0.001 A safe band or a durable interlock trips and rejects "
            "later mutations. The read-only power "
            "state exposes latch diagnostics. Reset is strictly out-of-band and is not "
            "available through MCP."
        )
    return (
        f"{physical_instruction} Never infer external wiring or no-load state from "
        "instrument mode. Observe configured total vacuum pressure and control one "
        "operator-bound Siglent SPD3000 topology through deterministic safety "
        "checks. Connection, channel, topology, identity, acceptance context, and "
        "ceilings are startup configuration and cannot be selected or raised by a "
        "tool call. Pressure is total gauge pressure, not rubidium partial pressure, "
        "and does not verify dispenser activation. Power readbacks do not verify "
        "activation either. This draft is not an unattended conditioning "
        "orchestrator or physical interlock."
    )


def _power_call[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except PowerControlError as error:
        raise ToolError(error.public_message) from error
