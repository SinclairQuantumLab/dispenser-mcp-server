"""Network-only Streamable HTTP startup for the supervised research pilot."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import webbrowser
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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
from dispenser_conditioning_mcp.learning_mode import (
    LearningModeService,
    learning_routes,
)
from dispenser_conditioning_mcp.recording_service import RecordingService

DEFAULT_HTTP_PATH = "/mcp"
MAX_HTTP_REQUEST_BODY_BYTES = 256 * 1024
LOGGER = logging.getLogger(__name__)


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
        learning = getattr(server, "learning", None)
        if isinstance(learning, LearningModeService):
            app.router.routes.extend(learning_routes(learning, access))
            original_lifespan = app.router.lifespan_context

            @asynccontextmanager
            async def learning_lifespan(
                application: Starlette,
            ) -> AsyncGenerator[None]:
                async with original_lifespan(application):
                    await learning.start()
                    try:
                        yield
                    finally:
                        await learning.stop()

            app.router.lifespan_context = learning_lifespan
    return app


def run_configured_transport(
    server: MCPServer[None], configuration: McpTransportConfiguration
) -> None:
    """Start the HTTP listener; this does not connect to an instrument."""
    app = create_http_app(server, configuration)
    access = getattr(app.state, "dashboard_access", None)
    run_http_application(
        app,
        configuration,
        access if isinstance(access, DashboardAccess) else None,
    )


def run_http_application(
    app: Starlette,
    configuration: McpTransportConfiguration,
    access: DashboardAccess | None,
) -> None:
    """Run one HTTP app and make one best-effort local GUI bootstrap attempt."""

    if access is not None:
        access.announce(configuration.port)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=configuration.bind_host,
            port=configuration.port,
            proxy_headers=False,
        )
    )
    cancelled = threading.Event()
    opener: threading.Thread | None = None
    if access is not None and _gui_browser_available():
        opener = threading.Thread(
            target=_open_console_when_ready,
            args=(server, access, configuration.port, cancelled),
            name="operator-console-browser",
            daemon=True,
        )
        opener.start()
    try:
        server.run()
    finally:
        cancelled.set()
        if opener is not None:
            opener.join(timeout=0.5)


def _gui_browser_available() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return sys.platform == "darwin" or os.name == "nt"


def _open_console_when_ready(
    server: uvicorn.Server,
    access: DashboardAccess,
    port: int,
    cancelled: threading.Event,
) -> None:
    deadline = time.monotonic() + 30.0
    while not cancelled.is_set() and time.monotonic() < deadline:
        if server.started:
            url = access.bootstrap_url(port)
            if url is not None:
                try:
                    webbrowser.open(url, new=2)
                except Exception:
                    LOGGER.warning("Local operator-console browser open failed")
            return
        cancelled.wait(0.05)
