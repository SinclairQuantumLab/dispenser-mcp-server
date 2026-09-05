"""Local stdio MCP adapter for the hardware-free simulator."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .contract import tool_specs
from .metadata import SIMULATOR_VERSION
from .model import (
    HiddenSimulatorConfig,
    SimulatedDispenser,
    ToolRouter,
)


def config_from_environment(
    environment: Mapping[str, str] | None = None,
) -> HiddenSimulatorConfig:
    """Build startup-only policy without putting hidden values in tool inputs."""

    env = os.environ if environment is None else environment
    seed = env.get("DISPENSER_SIM_SEED", "")
    scenario = env.get("DISPENSER_SIM_SCENARIO", "")
    if not seed or not scenario:
        raise RuntimeError(
            "Simulator startup requires harness-selected hidden seed and scenario."
        )
    acceptance_context = env.get(
        "DISPENSER_SIM_ACCEPTANCE_CONTEXT", "production_dispenser"
    )
    try:
        return HiddenSimulatorConfig(
            seed=seed,
            scenario=scenario,
            acceptance_context=acceptance_context,
            control_enabled=env.get("DISPENSER_SIM_CONTROL_ENABLED", "true").lower()
            == "true",
            compliance_voltage_v=float(
                env.get("DISPENSER_SIM_COMPLIANCE_VOLTAGE_V", "10.0")
            ),
            max_load_current_a=float(
                env.get("DISPENSER_SIM_MAX_LOAD_CURRENT_A", "4.8")
            ),
            observer_file=env.get("DISPENSER_SIM_OBSERVER_FILE") or None,
        )
    except (TypeError, ValueError):
        raise RuntimeError("Simulator startup policy is invalid.") from None


def build_runtime(
    config: HiddenSimulatorConfig,
) -> tuple[ToolRouter, list[dict[str, Any]]]:
    simulator = SimulatedDispenser(config)
    return ToolRouter(simulator), tool_specs(config.acceptance_context)


async def run_stdio(config: HiddenSimulatorConfig) -> None:
    """Run the optional MCP SDK adapter.

    The dynamic model and test suite remain dependency-light.  Importing the
    SDK here prevents an absent SDK from weakening the no-hardware core.
    """

    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    from .recording import RecordingAdapter, create_recording_service

    service = create_recording_service(os.environ)
    if config.observer_file is None:
        config = replace(
            config, observer_file=str(service.directory / "observer.jsonl")
        )
    router, _ = build_runtime(config)
    adapter = RecordingAdapter(router, service)
    catalog = adapter.catalog

    async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        listed: list[types.Tool] = []
        for spec in catalog:
            annotation = spec["annotations"]
            listed.append(
                types.Tool(
                    name=spec["name"],
                    description=spec["description"],
                    input_schema=spec["inputSchema"],
                    output_schema=spec.get("outputSchema"),
                    annotations=types.ToolAnnotations(**annotation),
                )
            )
        return types.ListToolsResult(tools=listed)

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return await adapter.call(params.name, params.arguments)

    server = Server(
        "dispenser-conditioning-simulator",
        version=SIMULATOR_VERSION,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    try:
        config = config_from_environment()
        asyncio.run(run_stdio(config))
    except (RuntimeError, ValueError) as exc:
        # stderr only; stdout is reserved for MCP frames.  Hidden values and raw
        # environment contents are never included in this sanitized message.
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
