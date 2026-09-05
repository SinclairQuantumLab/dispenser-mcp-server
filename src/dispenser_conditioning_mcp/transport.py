"""Network-only Streamable HTTP startup for the supervised research pilot."""

from __future__ import annotations

from dataclasses import dataclass

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from dispenser_conditioning_mcp.config import McpStartupConfiguration
from dispenser_conditioning_mcp.dashboard import dashboard_routes
from dispenser_conditioning_mcp.dashboard_access import DashboardAccess
from dispenser_conditioning_mcp.recording_service import RecordingService

DEFAULT_HTTP_PATH = "/mcp"
MAX_HTTP_REQUEST_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class McpTransportConfiguration:
    """Derived listener settings; these are never MCP tool arguments."""

    allow_remote_access: bool = False
    port: int = 8000

    @classmethod
    def from_settings(
        cls, settings: McpStartupConfiguration
    ) -> McpTransportConfiguration:
        return cls(settings.allow_remote_access, settings.port)

    @property
    def bind_host(self) -> str:
        return "0.0.0.0" if self.allow_remote_access else "127.0.0.1"

    def transport_security(self) -> TransportSecuritySettings:
        """Use SDK exact Host checks only for the local listener."""
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=not self.allow_remote_access,
            allowed_hosts=[
                f"127.0.0.1:{self.port}",
                f"localhost:{self.port}",
            ],
            allowed_origins=[],
        )


class NativeClientOnlyMiddleware:
    """Reject browser Origin headers, including empty and null origins."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["path"].rstrip("/") == DEFAULT_HTTP_PATH
            and "origin" in Headers(scope=scope)
        ):
            await Response("Browser origins are not supported", status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


def create_http_app(
    server: MCPServer[None], configuration: McpTransportConfiguration
) -> Starlette:
    """Serve the six tools at the fixed path with bounded request bodies."""
    app = server.streamable_http_app(
        streamable_http_path=DEFAULT_HTTP_PATH,
        json_response=False,
        stateless_http=False,
        max_request_body_size=MAX_HTTP_REQUEST_BODY_BYTES,
        transport_security=configuration.transport_security(),
    )
    app.add_middleware(NativeClientOnlyMiddleware)
    recording = getattr(server, "recording", None)
    if isinstance(recording, RecordingService):
        access = DashboardAccess()
        app.state.dashboard_access = access
        app.router.routes.extend(dashboard_routes(recording.directory, access=access))
    return app


def run_configured_transport(
    server: MCPServer[None], configuration: McpTransportConfiguration
) -> None:
    """Start the HTTP listener; this does not connect to an instrument."""
    app = create_http_app(server, configuration)
    access = getattr(app.state, "dashboard_access", None)
    if isinstance(access, DashboardAccess):
        access.announce()
    uvicorn.run(
        app,
        host=configuration.bind_host,
        port=configuration.port,
        proxy_headers=False,
    )
