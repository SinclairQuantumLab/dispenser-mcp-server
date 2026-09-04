"""Startup-only MCP transport configuration and execution."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from dispenser_conditioning_mcp.config import (
    ConfigurationError,
    McpStartupConfiguration,
)

McpTransport = Literal["stdio", "streamable-http"]
HttpTrustMode = Literal[
    "loopback_only",
    "authenticated_ssh_tunnel",
    "authenticated_reverse_proxy",
]

DEFAULT_HTTP_BIND_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_HTTP_PATH = "/mcp"
MAX_HTTP_REQUEST_BODY_BYTES = 256 * 1024

_LOOPBACK_NAMES = frozenset({"localhost"})
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


@dataclass(frozen=True)
class McpTransportConfiguration:
    """Validated startup boundary for one MCP process transport."""

    transport: McpTransport
    bind_host: str | None = None
    port: int | None = None
    path: str | None = None
    trust_mode: HttpTrustMode | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_settings(
        cls, settings: McpStartupConfiguration
    ) -> McpTransportConfiguration:
        """Build a default-stdio, fail-closed transport policy from TOML."""

        if settings.transport == "stdio":
            return cls(transport="stdio")

        http = settings.streamable_http
        if http is None:
            raise ConfigurationError(
                "streamable_http settings are required for Streamable HTTP."
            )
        bind_host = http.bind_host
        if not _is_loopback_host(bind_host):
            raise ConfigurationError(
                "Streamable HTTP must bind to an explicit loopback host."
            )
        port = http.port
        path = _path(http.path)
        trust_mode = http.trust_mode
        configured_hosts = http.allowed_hosts
        configured_origins = http.allowed_origins
        if trust_mode in {"loopback_only", "authenticated_ssh_tunnel"}:
            if configured_hosts or configured_origins:
                raise ConfigurationError(
                    "Custom allowed hosts and origins are valid only for the "
                    "authenticated_reverse_proxy trust mode."
                )
            if trust_mode == "loopback_only" and settings.control_enabled:
                raise ConfigurationError(
                    "Streamable HTTP power control requires an operator-owned "
                    "authenticated SSH tunnel or reverse proxy."
                )
            allowed_hosts = _loopback_host_headers(bind_host, port)
            allowed_origins: tuple[str, ...] = ()
        else:
            if not configured_hosts:
                raise ConfigurationError(
                    "streamable_http.allowed_hosts is required for an "
                    "authenticated reverse proxy."
                )
            for host in configured_hosts:
                _validate_host_header(host)
            for origin in configured_origins:
                _validate_https_origin(origin)
            allowed_hosts = _deduplicate(
                (*_loopback_host_headers(bind_host, port), *configured_hosts)
            )
            allowed_origins = configured_origins

        return cls(
            transport="streamable-http",
            bind_host=bind_host,
            port=port,
            path=path,
            trust_mode=trust_mode,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )

    def transport_security(self) -> TransportSecuritySettings:
        """Build exact Host and Origin checks for Streamable HTTP."""

        if self.transport != "streamable-http":
            raise ConfigurationError(
                "HTTP transport security is unavailable for stdio."
            )
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(self.allowed_hosts),
            allowed_origins=list(self.allowed_origins),
        )


def run_configured_transport(
    server: MCPServer[None], configuration: McpTransportConfiguration
) -> None:
    """Run one validated transport without exposing it to MCP tools."""

    if configuration.transport == "stdio":
        server.run(transport="stdio")
        return
    assert configuration.bind_host is not None
    assert configuration.port is not None
    assert configuration.path is not None
    server.run(
        transport="streamable-http",
        host=configuration.bind_host,
        port=configuration.port,
        streamable_http_path=configuration.path,
        json_response=False,
        stateless_http=False,
        max_request_body_size=MAX_HTTP_REQUEST_BODY_BYTES,
        transport_security=configuration.transport_security(),
    )


def _is_loopback_host(host: str) -> bool:
    if (
        not host
        or host != host.strip()
        or any(character.isspace() for character in host)
    ):
        return False
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _path(value: str) -> str:
    if (
        not value.startswith("/")
        or value == "/"
        or value.endswith("/")
        or "//" in value
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ConfigurationError("streamable_http.path is invalid.")
    return value


def _loopback_host_headers(bind_host: str, port: int) -> tuple[str, ...]:
    if ":" in bind_host:
        return (f"[{bind_host}]:{port}", f"[{bind_host}]")
    return (f"{bind_host}:{port}", bind_host)


def _validate_host_header(host: str) -> None:
    if (
        len(host) > 260
        or any(character.isspace() or ord(character) < 32 for character in host)
        or any(forbidden in host for forbidden in ("*", "/", "\\", "@", "://"))
    ):
        raise ConfigurationError(
            "streamable_http.allowed_hosts contains an invalid exact Host value."
        )
    parsed = urlsplit(f"//{host}")
    try:
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(
            "streamable_http.allowed_hosts contains an invalid port."
        ) from error
    if parsed.username is not None or parsed.password is not None or parsed.path:
        raise ConfigurationError(
            "streamable_http.allowed_hosts contains an invalid exact Host value."
        )
    hostname = parsed.hostname
    if hostname is None or (port is not None and not 1 <= port <= 65535):
        raise ConfigurationError(
            "streamable_http.allowed_hosts contains an invalid exact Host value."
        )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.rstrip(".").split(".")
        if any(_HOST_LABEL.fullmatch(label) is None for label in labels):
            raise ConfigurationError(
                "streamable_http.allowed_hosts contains an invalid hostname."
            )


def _validate_https_origin(origin: str) -> None:
    if "*" in origin or any(
        character.isspace() or ord(character) < 32 for character in origin
    ):
        raise ConfigurationError(
            "streamable_http.allowed_origins contains an invalid origin."
        )
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(
            "streamable_http.allowed_origins contains an invalid port."
        ) from error
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ConfigurationError(
            "streamable_http.allowed_origins must contain exact HTTPS origins."
        )


def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
