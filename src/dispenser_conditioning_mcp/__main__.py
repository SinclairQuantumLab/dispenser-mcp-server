"""Executable entry point for the Dispenser Conditioning MCP server."""

from __future__ import annotations

import logging
import sys

import uvicorn

from dispenser_conditioning_mcp.config import ConfigurationError
from dispenser_conditioning_mcp.runtime_coordinator import (
    RuntimeCoordinator,
    bootstrap_transport,
    create_runtime_http_app,
)


def main() -> None:
    """Validate operator configuration and run the HTTP listener."""

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        transport_configuration = bootstrap_transport()
        coordinator = RuntimeCoordinator()
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        raise SystemExit(2) from error

    app = create_runtime_http_app(coordinator, transport_configuration)
    coordinator.access.announce()
    uvicorn.run(
        app,
        host=transport_configuration.bind_host,
        port=transport_configuration.port,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
