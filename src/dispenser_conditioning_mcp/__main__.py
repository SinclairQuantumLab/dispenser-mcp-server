"""Executable entry point for the Dispenser Conditioning MCP server."""

from __future__ import annotations

import logging
import sys

from dispenser_conditioning_mcp.app import create_configured_server
from dispenser_conditioning_mcp.config import ConfigurationError, OperatorConfiguration
from dispenser_conditioning_mcp.transport import (
    McpTransportConfiguration,
    run_configured_transport,
)


def main() -> None:
    """Validate operator configuration and run the HTTP listener."""

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        operator = OperatorConfiguration.from_toml()
        transport_configuration = McpTransportConfiguration.from_settings(
            operator.startup
        )
        mcp = create_configured_server(operator)
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    run_configured_transport(mcp, transport_configuration)


if __name__ == "__main__":
    main()
