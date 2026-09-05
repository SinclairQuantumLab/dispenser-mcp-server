"""Strict operator-owned TOML configuration for the MCP process."""

from __future__ import annotations

import ipaddress
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from dispenser_conditioning_mcp.power_domain import PowerAcceptanceContext

SETTINGS_SCHEMA_VERSION = 1
DEFAULT_OPC_UA_PORT = 4840
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_SIGLENT_TIMEOUT_S = 5.0
DEFAULT_SIGLENT_COMMAND_INTERVAL_MS = 100.0
PARALLEL_NATIVE_CURRENT_CEILING_A = 2.4
PARALLEL_LOAD_CURRENT_CEILING_A = 4.8
PARALLEL_LOAD_UPWARD_STEP_A = 0.2
FIXED_SIGLENT_CONNECTION = "gateway"
FIXED_SIGLENT_TOPOLOGY = "parallel_ch1"
FIXED_SIGLENT_CHANNEL = "CH1"
FIXED_SIGLENT_EXPECTED_MODEL = "SPD3303X"

SiglentTopology = Literal["parallel_ch1"]
SiglentConnection = Literal["gateway"]
SiglentChannel = Literal["CH1"]


class ConfigurationError(ValueError):
    """Report invalid operator startup configuration without secret contents."""


@dataclass(frozen=True)
class SourceLayout:
    """Repository-owned paths resolved from the installed source checkout."""

    project_root: Path

    @classmethod
    def discover(cls) -> SourceLayout:
        """Resolve the checkout containing the editable installed package."""

        return cls(project_root=Path(__file__).resolve().parents[2])

    @classmethod
    def _for_testing(cls, project_root: Path) -> SourceLayout:
        """Build an explicit layout only for injected offline tests."""

        return cls(project_root=project_root.resolve())

    @property
    def settings_directory(self) -> Path:
        return self.project_root / "settings"

    @property
    def mcp_settings_file(self) -> Path:
        return self.settings_directory / "mcp-settings.toml"

    @property
    def hicube_settings_file(self) -> Path:
        return self.settings_directory / "hicube-neo-client-settings.toml"

    @property
    def siglent_settings_directory(self) -> Path:
        return self.settings_directory / "py-siglent-spd3000"

    @property
    def siglent_gateway_settings_file(self) -> Path:
        return self.siglent_settings_directory / "gateway-settings.toml"

    @property
    def siglent_gateway_auth_file(self) -> Path:
        return self.siglent_settings_directory / "gateway-auth.toml"

    @property
    def hicube_client_file(self) -> Path:
        return self.project_root / "dependencies" / "hicube" / "hicube_neo_client.py"

    @property
    def siglent_driver_src(self) -> Path:
        return self.project_root / "dependencies" / "py-siglent-spd3000" / "src"


@dataclass(frozen=True)
class McpStartupConfiguration:
    """MCP transport and deployment-specific safety settings."""

    acceptance_context: PowerAcceptanceContext
    expected_serial_number: str
    compliance_voltage_v: float
    control_enabled: bool = False
    allow_remote_access: bool = False
    port: int = 8000
    unloaded_hil_state_file: Path | None = None

    @classmethod
    def from_toml(cls, layout: SourceLayout) -> McpStartupConfiguration:
        """Load the closed main settings document."""

        document = _read_toml(layout.mcp_settings_file, "MCP settings")
        _closed_keys(
            document,
            {
                "schema_version",
                "backend",
                "simulation",
                "acceptance_context",
                "expected_serial_number",
                "compliance_voltage_v",
                "control_enabled",
                "allow_remote_access",
                "port",
                "unloaded_hil_state_file",
            },
            "MCP settings",
        )
        _schema_version(document, "MCP settings")
        if document.get("backend", "real") != "real":
            raise ConfigurationError("Hardware configuration requires backend = real")
        acceptance_context = cast(
            PowerAcceptanceContext,
            _choice(
                document.get("acceptance_context"),
                name="acceptance_context",
                choices=("production_dispenser", "unloaded_hil"),
            ),
        )
        expected_serial_number = _required_text(
            document.get("expected_serial_number"),
            name="expected_serial_number",
            maximum_length=128,
        )
        compliance_voltage_v = _required_number(
            document.get("compliance_voltage_v"),
            name="compliance_voltage_v",
            minimum=0.0,
            maximum=32.0,
        )
        _require_resolution(
            compliance_voltage_v,
            resolution=0.001,
            name="compliance_voltage_v",
        )
        control_enabled = _boolean(
            document.get("control_enabled", False), name="control_enabled"
        )
        allow_remote_access = _boolean(
            document.get("allow_remote_access", False), name="allow_remote_access"
        )
        port = _integer(
            document.get("port", 8000), name="port", minimum=1024, maximum=65535
        )
        unloaded_hil_state_file = _unloaded_hil_state_file(
            document.get("unloaded_hil_state_file"),
            acceptance_context=acceptance_context,
        )
        return cls(
            acceptance_context=acceptance_context,
            expected_serial_number=expected_serial_number,
            compliance_voltage_v=compliance_voltage_v,
            control_enabled=control_enabled,
            allow_remote_access=allow_remote_access,
            port=port,
            unloaded_hil_state_file=unloaded_hil_state_file,
        )


@dataclass(frozen=True)
class HiCubeConfiguration:
    """Validated HiCube settings and the fixed vendored client path."""

    client_file: Path
    host: str
    port: int = DEFAULT_OPC_UA_PORT
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_toml(cls, layout: SourceLayout) -> HiCubeConfiguration:
        document = _read_toml(layout.hicube_settings_file, "HiCube settings")
        _closed_keys(
            document,
            {"schema_version", "host", "port", "timeout_s"},
            "HiCube settings",
        )
        _schema_version(document, "HiCube settings")
        client_file = layout.hicube_client_file
        if client_file.name != "hicube_neo_client.py" or not client_file.is_file():
            raise ConfigurationError(
                "The repository-owned HiCube client file is missing."
            )
        return cls(
            client_file=client_file.resolve(),
            host=_host(document.get("host")),
            port=_integer(
                document.get("port", DEFAULT_OPC_UA_PORT),
                name="port",
                minimum=1,
                maximum=65535,
            ),
            timeout_s=_number(
                document.get("timeout_s", DEFAULT_TIMEOUT_S),
                name="timeout_s",
                minimum=0.1,
                maximum=60.0,
            ),
        )


@dataclass(frozen=True)
class SiglentConfiguration:
    """Validated SPD3000 settings and immutable operator safety policy."""

    driver_src: Path
    connection: SiglentConnection
    identifier: str
    gateway_auth_file: Path
    acceptance_context: PowerAcceptanceContext
    topology: SiglentTopology
    channel: SiglentChannel
    expected_model: str
    expected_serial_number: str
    compliance_voltage_v: float
    max_load_current_a: float
    upward_step_a: float
    control_enabled: bool
    unloaded_hil_state_file: Path | None = None
    timeout_s: float = DEFAULT_SIGLENT_TIMEOUT_S
    min_command_interval_ms: float = DEFAULT_SIGLENT_COMMAND_INTERVAL_MS

    @property
    def load_current_factor(self) -> Literal[2]:
        return 2

    @classmethod
    def from_toml(
        cls,
        layout: SourceLayout,
        startup: McpStartupConfiguration,
    ) -> SiglentConfiguration:
        document = _read_toml(
            layout.siglent_gateway_settings_file, "Siglent gateway settings"
        )
        _closed_keys(
            document,
            {
                "schema_version",
                "identifier",
                "timeout_s",
                "minimum_command_interval_ms",
            },
            "Siglent gateway settings",
        )
        _schema_version(document, "Siglent gateway settings")
        driver_src = layout.siglent_driver_src
        if not (driver_src / "siglent_spd3000" / "__init__.py").is_file():
            raise ConfigurationError(
                "The repository-owned Siglent driver submodule is missing."
            )
        auth_file = layout.siglent_gateway_auth_file
        if auth_file.name != "gateway-auth.toml" or not auth_file.is_file():
            raise ConfigurationError(
                "The operator-owned Siglent gateway authentication file is missing. "
                "Copy settings/py-siglent-spd3000/gateway-auth.toml.template to "
                "settings/py-siglent-spd3000/gateway-auth.toml and fill it locally."
            )
        return cls(
            driver_src=driver_src.resolve(),
            connection="gateway",
            identifier=_required_text(
                document.get("identifier"),
                name="identifier",
                maximum_length=512,
            ),
            gateway_auth_file=auth_file.resolve(),
            acceptance_context=startup.acceptance_context,
            topology="parallel_ch1",
            channel="CH1",
            expected_model="SPD3303X",
            expected_serial_number=startup.expected_serial_number,
            compliance_voltage_v=startup.compliance_voltage_v,
            max_load_current_a=PARALLEL_LOAD_CURRENT_CEILING_A,
            upward_step_a=PARALLEL_LOAD_UPWARD_STEP_A,
            control_enabled=startup.control_enabled,
            unloaded_hil_state_file=startup.unloaded_hil_state_file,
            timeout_s=_number(
                document.get("timeout_s", DEFAULT_SIGLENT_TIMEOUT_S),
                name="timeout_s",
                minimum=0.1,
                maximum=60.0,
            ),
            min_command_interval_ms=_number(
                document.get(
                    "minimum_command_interval_ms",
                    DEFAULT_SIGLENT_COMMAND_INTERVAL_MS,
                ),
                name="minimum_command_interval_ms",
                minimum=10.0,
                maximum=100.0,
            ),
        )


@dataclass(frozen=True)
class OperatorConfiguration:
    """One coherent snapshot of all three operator settings documents."""

    layout: SourceLayout
    startup: McpStartupConfiguration
    hicube: HiCubeConfiguration
    siglent: SiglentConfiguration

    @classmethod
    def from_toml(cls, layout: SourceLayout | None = None) -> OperatorConfiguration:
        resolved_layout = SourceLayout.discover() if layout is None else layout
        startup = McpStartupConfiguration.from_toml(resolved_layout)
        hicube = HiCubeConfiguration.from_toml(resolved_layout)
        siglent = SiglentConfiguration.from_toml(resolved_layout, startup)
        return cls(
            layout=resolved_layout,
            startup=startup,
            hicube=hicube,
            siglent=siglent,
        )


def _read_toml(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise ConfigurationError(f"{label} file is missing.")
    try:
        with path.open("rb") as settings_file:
            document = tomllib.load(settings_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError(f"{label} file is unreadable or invalid.") from error
    return cast(dict[str, object], document)


def _closed_keys(document: Mapping[str, object], allowed: set[str], label: str) -> None:
    if set(document) - allowed:
        raise ConfigurationError(f"{label} contains an unknown setting.")


def _schema_version(document: Mapping[str, object], label: str) -> None:
    value = document.get("schema_version")
    if type(value) is not int or value != SETTINGS_SCHEMA_VERSION:
        raise ConfigurationError(
            f"{label} schema_version must equal {SETTINGS_SCHEMA_VERSION}."
        )


def _unloaded_hil_state_file(
    raw_value: object,
    *,
    acceptance_context: PowerAcceptanceContext,
) -> Path | None:
    if acceptance_context == "production_dispenser":
        if raw_value is not None:
            raise ConfigurationError(
                "unloaded_hil_state_file is only valid for unloaded_hil."
            )
        return None
    if raw_value is None:
        raise ConfigurationError(
            "unloaded_hil_state_file is required for unloaded_hil."
        )
    value = _required_text(
        raw_value, name="unloaded_hil_state_file", maximum_length=1024
    )
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError("unloaded_hil_state_file must be absolute.")
    if path.name in {"", ".", ".."} or path.suffix.lower() != ".json":
        raise ConfigurationError(
            "unloaded_hil_state_file must identify a JSON record file."
        )
    if not path.parent.is_dir():
        raise ConfigurationError(
            "unloaded_hil_state_file must have an existing parent directory."
        )
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ConfigurationError(
            "unloaded_hil_state_file must identify a regular non-symlink file."
        )
    return path.resolve()


def _host(raw_value: object) -> str:
    host = _required_text(raw_value, name="host", maximum_length=253)
    if "://" in host or "/" in host or "\\" in host or any(c.isspace() for c in host):
        raise ConfigurationError("host must be one bare hostname or IP literal.")
    if ":" in host:
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError as error:
            raise ConfigurationError(
                "host may not contain an embedded port."
            ) from error
        if parsed.version != 6:
            raise ConfigurationError("host is invalid.")
    return host


def _integer(raw_value: object, *, name: str, minimum: int, maximum: int) -> int:
    if type(raw_value) is not int:
        raise ConfigurationError(f"{name} must be an integer.")
    value = raw_value
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _number(raw_value: object, *, name: str, minimum: float, maximum: float) -> float:
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        if _is_placeholder(raw_value):
            raise ConfigurationError(f"{name} still contains a placeholder.")
        raise ConfigurationError(f"{name} must be a TOML number.")
    value = float(raw_value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _required_number(
    raw_value: object, *, name: str, minimum: float, maximum: float
) -> float:
    if raw_value is None:
        raise ConfigurationError(f"{name} is required.")
    return _number(raw_value, name=name, minimum=minimum, maximum=maximum)


def _text(raw_value: object, *, name: str, maximum_length: int) -> str:
    if not isinstance(raw_value, str):
        raise ConfigurationError(f"{name} must be a string.")
    if (
        not raw_value
        or raw_value != raw_value.strip()
        or len(raw_value) > maximum_length
        or any(ord(character) < 32 for character in raw_value)
    ):
        raise ConfigurationError(f"{name} is invalid.")
    return raw_value


def _required_text(raw_value: object, *, name: str, maximum_length: int) -> str:
    if raw_value is None:
        raise ConfigurationError(f"{name} is required.")
    value = _text(raw_value, name=name, maximum_length=maximum_length)
    if _is_placeholder(value):
        raise ConfigurationError(f"{name} still contains a placeholder.")
    return value


def _is_placeholder(value: object) -> bool:
    return isinstance(value, str) and "replace-with-" in value.lower()


def _choice(raw_value: object, *, name: str, choices: tuple[str, ...]) -> str:
    value = _required_text(raw_value, name=name, maximum_length=64)
    if value not in choices:
        rendered = ", ".join(choices)
        raise ConfigurationError(f"{name} must be one of: {rendered}.")
    return value


def _boolean(raw_value: object, *, name: str) -> bool:
    if type(raw_value) is not bool:
        raise ConfigurationError(f"{name} must be a TOML boolean.")
    return raw_value


def _require_resolution(value: float, *, resolution: float, name: str) -> None:
    decimal_value = Decimal(str(value))
    decimal_resolution = Decimal(str(resolution))
    if decimal_value % decimal_resolution != 0:
        raise ConfigurationError(
            f"{name} must align to the fixed model resolution {resolution}."
        )
