"""Pressure observation and bounded power-control MCP for conditioning."""

from typing import Any

__all__ = ["create_server"]


def __getattr__(name: str) -> Any:
    if name == "create_server":
        from dispenser_conditioning_mcp.server import create_server

        return create_server
    raise AttributeError(name)
