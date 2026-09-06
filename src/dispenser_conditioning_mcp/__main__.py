"""Executable entry point for the Dispenser Conditioning MCP server."""

from __future__ import annotations

import logging
import sys

from dispenser_conditioning_mcp.backend import create_startup_server
from dispenser_conditioning_mcp.config import ConfigurationError
from dispenser_conditioning_mcp.transport import (
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
        mcp, transport_configuration = create_startup_server(check_hardware=True)
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    run_configured_transport(mcp, transport_configuration)


if __name__ == "__main__":
    main()
