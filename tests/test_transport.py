import threading
from unittest.mock import patch

import httpx
import pytest
from mcp.server import MCPServer
from starlette.applications import Starlette

from dispenser_conditioning_mcp.config import McpStartupConfiguration
from dispenser_conditioning_mcp.dashboard_access import DashboardAccess
from dispenser_conditioning_mcp.transport import (
    MAX_HTTP_REQUEST_BODY_BYTES,
    McpTransportConfiguration,
    create_http_app,
    run_configured_transport,
    run_http_application,
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
    with (
        patch("dispenser_conditioning_mcp.transport.uvicorn.Config") as config,
        patch("dispenser_conditioning_mcp.transport.uvicorn.Server") as server,
    ):
        run_configured_transport(MCPServer("offline-test"), configuration)
    assert config.call_args.kwargs == {
        "host": host,
        "port": 8123,
        "proxy_headers": False,
    }
    server.assert_called_once_with(config.return_value)
    server.return_value.run.assert_called_once_with()


def test_http_runner_opens_one_bootstrap_link_only_after_server_ready(
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened = threading.Event()

    class ReadyServer:
        started = False

        def __init__(self, _config: object) -> None:
            pass

        def run(self) -> None:
            self.started = True
            assert opened.wait(1)

    def open_browser(_url: str, *, new: int) -> bool:
        assert new == 2
        opened.set()
        return True

    access = DashboardAccess()
    with (
        patch("dispenser_conditioning_mcp.transport.uvicorn.Config"),
        patch("dispenser_conditioning_mcp.transport.uvicorn.Server", ReadyServer),
        patch(
            "dispenser_conditioning_mcp.transport._gui_browser_available",
            return_value=True,
        ),
        patch(
            "dispenser_conditioning_mcp.transport.webbrowser.open",
            side_effect=open_browser,
        ) as browser,
    ):
        run_http_application(Starlette(), McpTransportConfiguration(port=8123), access)
    assert browser.call_count == 1
    assert browser.call_args.args[0].startswith(
        "http://127.0.0.1:8123/dashboard/bootstrap#"
    )
    assert "http://127.0.0.1:8123/dashboard/bootstrap#" in capsys.readouterr().err


def test_http_runner_skips_browser_without_gui_and_on_startup_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    access = DashboardAccess()
    with (
        patch("dispenser_conditioning_mcp.transport.uvicorn.Config"),
        patch("dispenser_conditioning_mcp.transport.uvicorn.Server") as server,
        patch(
            "dispenser_conditioning_mcp.transport._gui_browser_available",
            return_value=False,
        ),
        patch("dispenser_conditioning_mcp.transport.webbrowser.open") as browser,
    ):
        run_http_application(Starlette(), McpTransportConfiguration(port=8124), access)
    browser.assert_not_called()
    server.return_value.run.assert_called_once_with()
    assert "http://127.0.0.1:8124/dashboard/bootstrap#" in capsys.readouterr().err

    failing_access = DashboardAccess()
    with (
        patch("dispenser_conditioning_mcp.transport.uvicorn.Config"),
        patch("dispenser_conditioning_mcp.transport.uvicorn.Server") as server,
        patch(
            "dispenser_conditioning_mcp.transport._gui_browser_available",
            return_value=True,
        ),
        patch("dispenser_conditioning_mcp.transport.webbrowser.open") as browser,
    ):
        server.return_value.started = False
        server.return_value.run.side_effect = RuntimeError("startup failed")
        with pytest.raises(RuntimeError, match="startup failed"):
            run_http_application(
                Starlette(), McpTransportConfiguration(port=8125), failing_access
            )
    browser.assert_not_called()


def test_linux_browser_detection_skips_headless_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dispenser_conditioning_mcp.transport.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from dispenser_conditioning_mcp.transport import (
        _gui_browser_available,  # pyright: ignore[reportPrivateUsage]
    )

    assert _gui_browser_available() is False
    monkeypatch.setenv("DISPLAY", ":0")
    assert _gui_browser_available() is True


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
