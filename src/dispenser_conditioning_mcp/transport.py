"""Startup-only MCP transport configuration and execution."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from dispenser_conditioning_mcp.config import ConfigurationError

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
_HTTP_ONLY_VARIABLES = (
    "DISPENSER_MCP_HTTP_BIND_HOST",
    "DISPENSER_MCP_HTTP_PORT",
    "DISPENSER_MCP_HTTP_PATH",
    "DISPENSER_MCP_HTTP_TRUST_MODE",
    "DISPENSER_MCP_HTTP_ALLOWED_HOSTS",
    "DISPENSER_MCP_HTTP_ALLOWED_ORIGINS",
)
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
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> McpTransportConfiguration:
        """Load a default-stdio, fail-closed transport policy."""

        import os

        values = os.environ if environment is None else environment
        raw_transport = values.get("DISPENSER_MCP_TRANSPORT", "stdio")
        if raw_transport not in {"stdio", "streamable-http"}:
            raise ConfigurationError(
                "DISPENSER_MCP_TRANSPORT must be stdio or streamable-http."
            )
        if raw_transport == "stdio":
            configured_http_variables = [
                name for name in _HTTP_ONLY_VARIABLES if values.get(name) is not None
            ]
            if configured_http_variables:
                raise ConfigurationError(
                    "HTTP-only transport settings are invalid when "
                    "DISPENSER_MCP_TRANSPORT is stdio."
                )
            return cls(transport="stdio")

        control_enabled = _required_boolean(
            values.get("DISPENSER_SIGLENT_CONTROL_ENABLED"),
            name="DISPENSER_SIGLENT_CONTROL_ENABLED",
        )
        bind_host = values.get("DISPENSER_MCP_HTTP_BIND_HOST", DEFAULT_HTTP_BIND_HOST)
        if not _is_loopback_host(bind_host):
            raise ConfigurationError(
                "Streamable HTTP must bind to an explicit loopback host."
            )
        port = _port(values.get("DISPENSER_MCP_HTTP_PORT"))
        path = _path(values.get("DISPENSER_MCP_HTTP_PATH"))
        raw_trust_mode = values.get("DISPENSER_MCP_HTTP_TRUST_MODE", "loopback_only")
        if raw_trust_mode not in {
            "loopback_only",
            "authenticated_ssh_tunnel",
            "authenticated_reverse_proxy",
        }:
            raise ConfigurationError(
                "DISPENSER_MCP_HTTP_TRUST_MODE must be loopback_only, "
                "authenticated_ssh_tunnel, or authenticated_reverse_proxy."
            )
        trust_mode = cast(HttpTrustMode, raw_trust_mode)

        configured_hosts = _csv_values(
            values.get("DISPENSER_MCP_HTTP_ALLOWED_HOSTS"),
            name="DISPENSER_MCP_HTTP_ALLOWED_HOSTS",
        )
        configured_origins = _csv_values(
            values.get("DISPENSER_MCP_HTTP_ALLOWED_ORIGINS"),
            name="DISPENSER_MCP_HTTP_ALLOWED_ORIGINS",
        )
        if trust_mode in {"loopback_only", "authenticated_ssh_tunnel"}:
            if configured_hosts or configured_origins:
                raise ConfigurationError(
                    "Custom allowed hosts and origins are valid only for the "
                    "authenticated_reverse_proxy trust mode."
                )
            if trust_mode == "loopback_only" and control_enabled:
                raise ConfigurationError(
                    "Streamable HTTP power control requires an operator-owned "
                    "authenticated SSH tunnel or reverse proxy."
                )
            allowed_hosts = _loopback_host_headers(bind_host, port)
            allowed_origins: tuple[str, ...] = ()
        else:
            if not configured_hosts:
                raise ConfigurationError(
                    "DISPENSER_MCP_HTTP_ALLOWED_HOSTS is required for an "
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


def _required_boolean(raw_value: str | None, *, name: str) -> bool:
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise ConfigurationError(f"{name} must be explicitly true or false.")


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


def _port(raw_value: str | None) -> int:
    if raw_value is None:
        return DEFAULT_HTTP_PORT
    if not raw_value or raw_value != raw_value.strip():
        raise ConfigurationError("DISPENSER_MCP_HTTP_PORT must be an integer.")
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(
            "DISPENSER_MCP_HTTP_PORT must be an integer."
        ) from error
    if not 1024 <= value <= 65535:
        raise ConfigurationError(
            "DISPENSER_MCP_HTTP_PORT must be between 1024 and 65535."
        )
    return value


def _path(raw_value: str | None) -> str:
    value = DEFAULT_HTTP_PATH if raw_value is None else raw_value
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
        raise ConfigurationError("DISPENSER_MCP_HTTP_PATH is invalid.")
    return value


def _csv_values(raw_value: str | None, *, name: str) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    if not raw_value or raw_value != raw_value.strip():
        raise ConfigurationError(f"{name} is invalid.")
    values = tuple(item.strip() for item in raw_value.split(","))
    if any(not item for item in values):
        raise ConfigurationError(f"{name} contains an empty value.")
    if len(set(values)) != len(values):
        raise ConfigurationError(f"{name} contains a duplicate value.")
    return values


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
            "DISPENSER_MCP_HTTP_ALLOWED_HOSTS contains an invalid exact Host value."
        )
    parsed = urlsplit(f"//{host}")
    try:
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(
            "DISPENSER_MCP_HTTP_ALLOWED_HOSTS contains an invalid port."
        ) from error
    if parsed.username is not None or parsed.password is not None or parsed.path:
        raise ConfigurationError(
            "DISPENSER_MCP_HTTP_ALLOWED_HOSTS contains an invalid exact Host value."
        )
    hostname = parsed.hostname
    if hostname is None or (port is not None and not 1 <= port <= 65535):
        raise ConfigurationError(
            "DISPENSER_MCP_HTTP_ALLOWED_HOSTS contains an invalid exact Host value."
        )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.rstrip(".").split(".")
        if any(_HOST_LABEL.fullmatch(label) is None for label in labels):
            raise ConfigurationError(
                "DISPENSER_MCP_HTTP_ALLOWED_HOSTS contains an invalid hostname."
            )


def _validate_https_origin(origin: str) -> None:
    if "*" in origin or any(
        character.isspace() or ord(character) < 32 for character in origin
    ):
        raise ConfigurationError(
            "DISPENSER_MCP_HTTP_ALLOWED_ORIGINS contains an invalid origin."
        )
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(
            "DISPENSER_MCP_HTTP_ALLOWED_ORIGINS contains an invalid port."
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
            "DISPENSER_MCP_HTTP_ALLOWED_ORIGINS must contain exact HTTPS origins."
        )


def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
