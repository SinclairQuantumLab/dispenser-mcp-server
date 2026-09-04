from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from mcp.server import MCPServer

from dispenser_conditioning_mcp.config import (
    ConfigurationError,
    McpStartupConfiguration,
    StreamableHttpSettings,
)
from dispenser_conditioning_mcp.transport import (
    MAX_HTTP_REQUEST_BODY_BYTES,
    McpTransportConfiguration,
    run_configured_transport,
)


def http_settings(**overrides: object) -> McpStartupConfiguration:
    values: dict[str, object] = {
        "acceptance_context": "production_dispenser",
        "expected_serial_number": "SPD-OFFLINE",
        "compliance_voltage_v": 10.0,
        "control_enabled": False,
        "transport": "streamable-http",
        "streamable_http": StreamableHttpSettings(),
    }
    values.update(overrides)
    return McpStartupConfiguration(**values)  # type: ignore[arg-type]


def test_transport_defaults_to_stdio() -> None:
    settings = McpStartupConfiguration(
        acceptance_context="production_dispenser",
        expected_serial_number="SPD-OFFLINE",
        compliance_voltage_v=10.0,
    )
    configuration = McpTransportConfiguration.from_settings(settings)

    assert configuration == McpTransportConfiguration(transport="stdio")


def test_streamable_http_defaults_are_loopback_and_read_only() -> None:
    configuration = McpTransportConfiguration.from_settings(http_settings())

    assert configuration.transport == "streamable-http"
    assert configuration.bind_host == "127.0.0.1"
    assert configuration.port == 8000
    assert configuration.path == "/mcp"
    assert configuration.trust_mode == "loopback_only"
    assert configuration.allowed_hosts == ("127.0.0.1:8000", "127.0.0.1")
    assert configuration.allowed_origins == ()


def test_loopback_only_http_rejects_control_enabled() -> None:
    with pytest.raises(ConfigurationError, match="authenticated SSH tunnel"):
        McpTransportConfiguration.from_settings(http_settings(control_enabled=True))


def test_authenticated_ssh_tunnel_allows_explicit_control_on_loopback() -> None:
    http = StreamableHttpSettings(trust_mode="authenticated_ssh_tunnel")
    configuration = McpTransportConfiguration.from_settings(
        http_settings(control_enabled=True, streamable_http=http)
    )
    assert configuration.bind_host == "127.0.0.1"
    assert configuration.trust_mode == "authenticated_ssh_tunnel"


def test_authenticated_proxy_allows_explicit_control_and_exact_headers() -> None:
    http = StreamableHttpSettings(
        trust_mode="authenticated_reverse_proxy",
        allowed_hosts=("mcp.example.test", "mcp.example.test:443"),
        allowed_origins=("https://console.example.test",),
    )
    configuration = McpTransportConfiguration.from_settings(
        http_settings(control_enabled=True, streamable_http=http)
    )

    assert configuration.allowed_hosts == (
        "127.0.0.1:8000",
        "127.0.0.1",
        "mcp.example.test",
        "mcp.example.test:443",
    )
    assert configuration.allowed_origins == ("https://console.example.test",)


def test_authenticated_proxy_requires_exact_allowed_hosts() -> None:
    http = StreamableHttpSettings(trust_mode="authenticated_reverse_proxy")
    with pytest.raises(ConfigurationError, match="allowed_hosts is required"):
        McpTransportConfiguration.from_settings(http_settings(streamable_http=http))


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.0.2.4", "mcp.example.test", "127.0.0.1:8000"],
)
def test_streamable_http_rejects_non_loopback_bind(host: str) -> None:
    http = StreamableHttpSettings(bind_host=host)
    with pytest.raises(ConfigurationError, match="explicit loopback host"):
        McpTransportConfiguration.from_settings(http_settings(streamable_http=http))


@pytest.mark.parametrize("path", ["mcp", "/", "/mcp/", "//mcp", "/mcp?q=1"])
def test_streamable_http_rejects_invalid_path(path: str) -> None:
    http = StreamableHttpSettings(path=path)
    with pytest.raises(ConfigurationError, match="path is invalid"):
        McpTransportConfiguration.from_settings(http_settings(streamable_http=http))


@pytest.mark.parametrize(
    ("http", "message"),
    [
        (
            StreamableHttpSettings(
                trust_mode="authenticated_reverse_proxy",
                allowed_hosts=("*.example.test",),
            ),
            "invalid exact Host",
        ),
        (
            StreamableHttpSettings(
                trust_mode="authenticated_reverse_proxy",
                allowed_hosts=("mcp.example.test",),
                allowed_origins=("http://console.example.test",),
            ),
            "HTTPS origins",
        ),
    ],
)
def test_authenticated_proxy_rejects_inexact_headers(
    http: StreamableHttpSettings, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        McpTransportConfiguration.from_settings(http_settings(streamable_http=http))


def test_run_configured_transport_preserves_stdio_contract() -> None:
    server: MCPServer[None] = MCPServer("offline-test")
    configuration = McpTransportConfiguration(transport="stdio")

    with patch.object(MCPServer, "run", autospec=True) as run:
        run_configured_transport(server, configuration)

    run.assert_called_once_with(server, transport="stdio")


def test_run_configured_transport_passes_bounded_http_settings() -> None:
    server: MCPServer[None] = MCPServer("offline-test")
    configuration = McpTransportConfiguration.from_settings(http_settings())

    with patch.object(MCPServer, "run", autospec=True) as run:
        run_configured_transport(server, configuration)

    call = run.call_args
    assert call.args == (server,)
    assert call.kwargs["transport"] == "streamable-http"
    assert call.kwargs["host"] == "127.0.0.1"
    assert call.kwargs["port"] == 8000
    assert call.kwargs["streamable_http_path"] == "/mcp"
    assert call.kwargs["max_request_body_size"] == MAX_HTTP_REQUEST_BODY_BYTES


@pytest.mark.anyio
async def test_http_app_rejects_unlisted_host_before_mcp_dispatch() -> None:
    configuration = McpTransportConfiguration.from_settings(http_settings())
    server: MCPServer[None] = MCPServer("offline-test")
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=configuration.transport_security(),
        max_request_body_size=MAX_HTTP_REQUEST_BODY_BYTES,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://unlisted.example.test",
        ) as client:
            response = await client.get("/mcp")

    assert response.status_code == 421


@pytest.mark.anyio
async def test_http_app_rejects_unlisted_origin_before_mcp_dispatch() -> None:
    configuration = McpTransportConfiguration.from_settings(http_settings())
    server: MCPServer[None] = MCPServer("offline-test")
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=configuration.transport_security(),
        max_request_body_size=MAX_HTTP_REQUEST_BODY_BYTES,
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client:
            response = await client.get(
                "/mcp", headers={"Origin": "https://unlisted.example.test"}
            )

    assert response.status_code == 403


def test_transport_configuration_has_no_model_or_settings_path_input() -> None:
    fields = McpTransportConfiguration.__dataclass_fields__
    assert "settings_path" not in fields
    assert "credential" not in fields
    assert Path not in {field.type for field in fields.values()}
