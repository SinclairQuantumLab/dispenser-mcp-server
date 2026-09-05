"""Operator-only backend selection; never fall back between hardware and model."""

from __future__ import annotations

import tomllib
from typing import Any

from mcp.server import MCPServer

from dispenser_conditioning_mcp.config import ConfigurationError, SourceLayout
from dispenser_conditioning_mcp.transport import McpTransportConfiguration


def create_startup_server(
    layout: SourceLayout | None = None,
) -> tuple[MCPServer[None], McpTransportConfiguration]:
    layout = layout or SourceLayout.discover()
    try:
        with layout.mcp_settings_file.open("rb") as stream:
            document: dict[str, Any] = tomllib.load(stream)
    except (OSError, ValueError) as error:
        raise ConfigurationError("Cannot read MCP startup settings") from error
    backend = document.get("backend", "hardware")
    if backend == "hardware":
        from dispenser_conditioning_mcp.app import create_configured_server
        from dispenser_conditioning_mcp.config import OperatorConfiguration

        operator = OperatorConfiguration.from_toml(layout)
        return create_configured_server(
            operator
        ), McpTransportConfiguration.from_settings(operator.startup)
    if backend != "simulation":
        raise ConfigurationError("backend must be hardware or simulation")
    allowed = {
        "backend",
        "allow_remote_access",
        "port",
        "simulation",
        "acceptance_context",
        "expected_serial_number",
        "compliance_voltage_v",
        "control_enabled",
    }
    if set(document) - allowed:
        raise ConfigurationError("Invalid simulation startup document")
    remote, port = (
        document.get("allow_remote_access", False),
        document.get("port", 8000),
    )
    if type(remote) is not bool or type(port) is not int or not 1024 <= port <= 65535:
        raise ConfigurationError("Invalid allow_remote_access or port")
    from dispenser_conditioning_mcp.simulation_app import create_simulation_server

    return create_simulation_server(
        document.get("simulation", {})
    ), McpTransportConfiguration(remote, port)
