from unittest.mock import patch

import httpx
import pytest
from mcp.server import MCPServer

from dispenser_conditioning_mcp.config import McpStartupConfiguration
from dispenser_conditioning_mcp.transport import (
    MAX_HTTP_REQUEST_BODY_BYTES,
    McpTransportConfiguration,
    create_http_app,
    run_configured_transport,
)


@pytest.mark.parametrize("remote,host", [(False, "127.0.0.1"), (True, "0.0.0.0")])
def test_startup_uses_http_and_requested_ipv4_listener(remote: bool, host: str) -> None:
    settings = McpStartupConfiguration(
        acceptance_context="production_dispenser",
        expected_serial_number="SPD-OFFLINE",
        compliance_voltage_v=10.0,
        control_enabled=True,
        allow_remote_access=remote,
        port=8123,
    )
    configuration = McpTransportConfiguration.from_settings(settings)
    with patch("dispenser_conditioning_mcp.transport.uvicorn.run") as run:
        run_configured_transport(MCPServer("offline-test"), configuration)
    assert run.call_args.kwargs == {"host": host, "port": 8123}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "remote,host,origin,status",
    [
        (False, "127.0.0.1:8000", None, 200),
        (False, "localhost:8000", None, 200),
        (False, "unlisted.example.test:8000", None, 421),
        (True, "192.0.2.4:8000", None, 200),
        (True, "raspberrypi.local:8000", None, 200),
        (True, "192.0.2.4:8000", "https://browser.example", 403),
        (True, "192.0.2.4:8000", "null", 403),
        (True, "192.0.2.4:8000", "", 403),
        (False, "127.0.0.1:8000", "http://127.0.0.1:8000", 403),
    ],
)
async def test_native_client_host_and_browser_origin_policy(
    remote: bool, host: str, origin: str | None, status: int
) -> None:
    server: MCPServer[None] = MCPServer("offline-test")
    app = create_http_app(server, McpTransportConfiguration(remote))
    headers = {"Accept": "application/json, text/event-stream"}
    if origin is not None:
        headers["Origin"] = origin
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"http://{host}"
        ) as client,
    ):
        response = await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "offline-test", "version": "1"},
                },
            },
        )
    assert response.status_code == status


@pytest.mark.anyio
async def test_http_keeps_fixed_path_content_type_and_body_limit() -> None:
    server: MCPServer[None] = MCPServer("offline-test")
    app = create_http_app(server, McpTransportConfiguration(True))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://192.0.2.4:8000"
        ) as client,
    ):
        assert (await client.get("/other")).status_code == 404
        assert (await client.post("/mcp", content="plain")).status_code == 400
        response = await client.post(
            "/mcp",
            content=b"x" * (MAX_HTTP_REQUEST_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
