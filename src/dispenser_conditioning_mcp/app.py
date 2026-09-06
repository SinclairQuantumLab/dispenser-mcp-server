"""Construct the configured MCP application from one TOML snapshot."""

from __future__ import annotations

from mcp.server import MCPServer

from dispenser_conditioning_mcp.config import OperatorConfiguration
from dispenser_conditioning_mcp.hicube import HiCubeNeoPressureSource
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.siglent import DispenserPowerController


def create_configured_server(
    configuration: OperatorConfiguration | None = None,
    *,
    check_hardware: bool = False,
) -> MCPServer[None]:
    """Build a server from operator-only TOML settings."""

    operator = (
        OperatorConfiguration.from_toml() if configuration is None else configuration
    )
    hicube = operator.hicube
    source = HiCubeNeoPressureSource(
        client_file=hicube.client_file,
        host=hicube.host,
        port=hicube.port,
        timeout_s=hicube.timeout_s,
    )
    power_controller = DispenserPowerController(operator.siglent)
    if check_hardware:
        from dispenser_conditioning_mcp.startup_check import check_connections

        check_connections(source, power_controller)
    return create_server(
        source,
        power_controller,
        reload_current_limit=lambda: power_controller.reload_current_limit(
            operator.layout
        ),
        initial_max_load_current_A=operator.startup.max_load_current_A,
    )
