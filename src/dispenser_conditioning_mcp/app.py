"""Configured MCP application object for development and transport startup."""

from __future__ import annotations

from mcp.server import MCPServer

from dispenser_conditioning_mcp.config import HiCubeConfiguration, SiglentConfiguration
from dispenser_conditioning_mcp.hicube import HiCubeNeoPressureSource
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.siglent import DispenserPowerController


def create_configured_server() -> MCPServer[None]:
    """Build a server from operator-only process environment variables."""

    configuration = HiCubeConfiguration.from_environment()
    source = HiCubeNeoPressureSource(
        client_file=configuration.client_file,
        host=configuration.host,
        port=configuration.port,
        timeout_s=configuration.timeout_s,
    )
    siglent_configuration = SiglentConfiguration.from_environment()
    power_controller = DispenserPowerController(siglent_configuration)
    return create_server(source, power_controller)


mcp = create_configured_server()
