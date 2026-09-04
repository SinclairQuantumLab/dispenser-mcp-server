from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import patch

import httpx
import pytest
from mcp.server import MCPServer

from dispenser_conditioning_mcp.config import ConfigurationError
from dispenser_conditioning_mcp.transport import (
    MAX_HTTP_REQUEST_BODY_BYTES,
    McpTransportConfiguration,
    run_configured_transport,
)


def http_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "DISPENSER_MCP_TRANSPORT": "streamable-http",
        "DISPENSER_SIGLENT_CONTROL_ENABLED": "false",
    }
    environment.update(overrides)
    return environment


def test_transport_defaults_to_stdio() -> None:
    configuration = McpTransportConfiguration.from_environment({})

    assert configuration == McpTransportConfiguration(transport="stdio")


@pytest.mark.parametrize("transport", ["sse", "http", "STREAMABLE-HTTP", ""])
def test_unknown_or_deprecated_transport_is_denied(transport: str) -> None:
    with pytest.raises(ConfigurationError, match="must be stdio or streamable-http"):
        McpTransportConfiguration.from_environment(
            {"DISPENSER_MCP_TRANSPORT": transport}
        )


def test_stdio_rejects_http_only_settings() -> None:
    with pytest.raises(ConfigurationError, match="HTTP-only transport settings"):
        McpTransportConfiguration.from_environment(
            {
                "DISPENSER_MCP_TRANSPORT": "stdio",
                "DISPENSER_MCP_HTTP_PORT": "8000",
            }
        )


def test_streamable_http_defaults_are_loopback_and_read_only() -> None:
    configuration = McpTransportConfiguration.from_environment(http_environment())

    assert configuration.transport == "streamable-http"
    assert configuration.bind_host == "127.0.0.1"
    assert configuration.port == 8000
    assert configuration.path == "/mcp"
    assert configuration.trust_mode == "loopback_only"
    assert configuration.allowed_hosts == ("127.0.0.1:8000", "127.0.0.1")
    assert configuration.allowed_origins == ()
    security = configuration.transport_security()
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["127.0.0.1:8000", "127.0.0.1"]
    assert security.allowed_origins == []


def test_streamable_http_requires_explicit_control_policy() -> None:
    with pytest.raises(ConfigurationError, match="must be explicitly true or false"):
        McpTransportConfiguration.from_environment(
            {"DISPENSER_MCP_TRANSPORT": "streamable-http"}
        )


def test_loopback_only_http_rejects_control_enabled() -> None:
    with pytest.raises(ConfigurationError, match="authenticated SSH tunnel"):
        McpTransportConfiguration.from_environment(
            http_environment(DISPENSER_SIGLENT_CONTROL_ENABLED="true")
        )


def test_authenticated_ssh_tunnel_allows_explicit_control_on_loopback() -> None:
    configuration = McpTransportConfiguration.from_environment(
        http_environment(
            DISPENSER_SIGLENT_CONTROL_ENABLED="true",
            DISPENSER_MCP_HTTP_TRUST_MODE="authenticated_ssh_tunnel",
        )
    )

    assert configuration.bind_host == "127.0.0.1"
    assert configuration.trust_mode == "authenticated_ssh_tunnel"
    assert configuration.allowed_hosts == ("127.0.0.1:8000", "127.0.0.1")


def test_authenticated_proxy_allows_explicit_control_and_exact_headers() -> None:
    configuration = McpTransportConfiguration.from_environment(
        http_environment(
            DISPENSER_SIGLENT_CONTROL_ENABLED="true",
            DISPENSER_MCP_HTTP_TRUST_MODE="authenticated_reverse_proxy",
            DISPENSER_MCP_HTTP_ALLOWED_HOSTS="mcp.example.test,mcp.example.test:443",
            DISPENSER_MCP_HTTP_ALLOWED_ORIGINS="https://console.example.test",
        )
    )

    assert configuration.allowed_hosts == (
        "127.0.0.1:8000",
        "127.0.0.1",
        "mcp.example.test",
        "mcp.example.test:443",
    )
    assert configuration.allowed_origins == ("https://console.example.test",)


def test_authenticated_proxy_requires_exact_allowed_hosts() -> None:
    with pytest.raises(ConfigurationError, match="ALLOWED_HOSTS is required"):
        McpTransportConfiguration.from_environment(
            http_environment(
                DISPENSER_MCP_HTTP_TRUST_MODE="authenticated_reverse_proxy"
            )
        )


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.0.2.4", "mcp.example.test", "127.0.0.1:8000"],
)
def test_streamable_http_rejects_non_loopback_bind(host: str) -> None:
    with pytest.raises(ConfigurationError, match="explicit loopback host"):
        McpTransportConfiguration.from_environment(
            http_environment(DISPENSER_MCP_HTTP_BIND_HOST=host)
        )


@pytest.mark.parametrize("port", ["80", "65536", "8000.0", " 8000", ""])
def test_streamable_http_rejects_invalid_port(port: str) -> None:
    with pytest.raises(ConfigurationError, match="HTTP_PORT"):
        McpTransportConfiguration.from_environment(
            http_environment(DISPENSER_MCP_HTTP_PORT=port)
        )


@pytest.mark.parametrize("path", ["mcp", "/", "/mcp/", "//mcp", "/mcp?q=1"])
def test_streamable_http_rejects_invalid_path(path: str) -> None:
    with pytest.raises(ConfigurationError, match="HTTP_PATH is invalid"):
        McpTransportConfiguration.from_environment(
            http_environment(DISPENSER_MCP_HTTP_PATH=path)
        )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("DISPENSER_MCP_HTTP_ALLOWED_HOSTS", "*.example.test", "invalid exact Host"),
        (
            "DISPENSER_MCP_HTTP_ALLOWED_HOSTS",
            "https://mcp.example.test",
            "invalid exact Host",
        ),
        ("DISPENSER_MCP_HTTP_ALLOWED_HOSTS", "bad_host", "invalid hostname"),
        (
            "DISPENSER_MCP_HTTP_ALLOWED_ORIGINS",
            "http://console.example.test",
            "HTTPS origins",
        ),
        (
            "DISPENSER_MCP_HTTP_ALLOWED_ORIGINS",
            "https://*.example.test",
            "invalid origin",
        ),
    ],
)
def test_authenticated_proxy_rejects_inexact_headers(
    name: str, value: str, message: str
) -> None:
    environment: dict[str, str] = http_environment(
        DISPENSER_MCP_HTTP_TRUST_MODE="authenticated_reverse_proxy",
        DISPENSER_MCP_HTTP_ALLOWED_HOSTS="mcp.example.test",
    )
    environment[name] = value

    with pytest.raises(ConfigurationError, match=message):
        McpTransportConfiguration.from_environment(environment)


def test_run_configured_transport_preserves_stdio_contract() -> None:
    server: MCPServer[None] = MCPServer("offline-test")

    with patch.object(MCPServer, "run", autospec=True) as run:
        run_configured_transport(server, McpTransportConfiguration.from_environment({}))

    run.assert_called_once_with(server, transport="stdio")


def test_run_configured_transport_passes_bounded_http_settings() -> None:
    server: MCPServer[None] = MCPServer("offline-test")
    configuration = McpTransportConfiguration.from_environment(http_environment())

    with patch.object(MCPServer, "run", autospec=True) as run:
        run_configured_transport(server, configuration)

    call = run.call_args
    assert call.args == (server,)
    assert call.kwargs["transport"] == "streamable-http"
    assert call.kwargs["host"] == "127.0.0.1"
    assert call.kwargs["port"] == 8000
    assert call.kwargs["streamable_http_path"] == "/mcp"
    assert call.kwargs["json_response"] is False
    assert call.kwargs["stateless_http"] is False
    assert call.kwargs["max_request_body_size"] == MAX_HTTP_REQUEST_BODY_BYTES
    security = call.kwargs["transport_security"]
    assert security.enable_dns_rebinding_protection is True


@pytest.mark.anyio
async def test_http_app_rejects_unlisted_host_before_mcp_dispatch() -> None:
    configuration = McpTransportConfiguration.from_environment(http_environment())
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
    assert response.text == "Invalid Host header"


@pytest.mark.anyio
async def test_http_app_rejects_unlisted_origin_before_mcp_dispatch() -> None:
    configuration = McpTransportConfiguration.from_environment(http_environment())
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
    assert response.text == "Invalid Origin header"


def test_transport_configuration_has_no_model_input_mapping() -> None:
    fields: Mapping[str, object] = McpTransportConfiguration.__dataclass_fields__

    assert set(fields) == {
        "transport",
        "bind_host",
        "port",
        "path",
        "trust_mode",
        "allowed_hosts",
        "allowed_origins",
    }
