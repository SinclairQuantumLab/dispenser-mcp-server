"""Operator-only startup configuration for pressure and power integrations."""

from __future__ import annotations

import ipaddress
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from dispenser_conditioning_mcp.power_domain import PowerAcceptanceContext

DEFAULT_OPC_UA_PORT = 4840
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_SIGLENT_TIMEOUT_S = 5.0
DEFAULT_SIGLENT_COMMAND_INTERVAL_MS = 100.0
DEFAULT_DEVELOPMENT_HICUBE_CLIENT_FILE = (
    Path(__file__).resolve().parents[2]
    / "dependencies"
    / "hicube"
    / "hicube_neo_client.py"
)
PARALLEL_NATIVE_CURRENT_CEILING_A = 2.4
PARALLEL_LOAD_CURRENT_CEILING_A = 2 * PARALLEL_NATIVE_CURRENT_CEILING_A
PARALLEL_LOAD_UPWARD_STEP_A = 0.2
DEFAULT_DEVELOPMENT_GATEWAY_AUTH_FILE = (
    Path(__file__).resolve().parents[2]
    / "settings"
    / "py-siglent-spd3000-gateway-auth.toml"
)

SiglentTopology = Literal["parallel_ch1"]
SiglentConnection = Literal["gateway"]
SiglentChannel = Literal["CH1"]


class ConfigurationError(ValueError):
    """Report invalid or missing operator startup configuration."""


@dataclass(frozen=True)
class HiCubeConfiguration:
    """Validated local settings that are never exposed as MCP tool arguments."""

    client_file: Path
    host: str
    port: int = DEFAULT_OPC_UA_PORT
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> HiCubeConfiguration:
        """Load validated settings from the process environment."""

        values = os.environ if environment is None else environment
        client_file = _client_file(values.get("DISPENSER_HICUBE_CLIENT_FILE"))
        host = _host(values.get("DISPENSER_HICUBE_HOST"))
        port = _integer(
            values.get("DISPENSER_HICUBE_PORT"),
            name="DISPENSER_HICUBE_PORT",
            default=DEFAULT_OPC_UA_PORT,
            minimum=1,
            maximum=65535,
        )
        timeout_s = _number(
            values.get("DISPENSER_HICUBE_TIMEOUT_S"),
            name="DISPENSER_HICUBE_TIMEOUT_S",
            default=DEFAULT_TIMEOUT_S,
            minimum=0.1,
            maximum=60.0,
        )
        return cls(
            client_file=client_file,
            host=host,
            port=port,
            timeout_s=timeout_s,
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
        """Translate one native-channel current setpoint to load-current limit."""

        return 2

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> SiglentConfiguration:
        """Load a default-deny power-control policy from the environment."""

        values = os.environ if environment is None else environment
        driver_src = _driver_src(values.get("DISPENSER_SIGLENT_DRIVER_SRC"))
        connection = _choice(
            values.get("DISPENSER_SIGLENT_CONNECTION"),
            name="DISPENSER_SIGLENT_CONNECTION",
            choices=("gateway",),
        )
        identifier = _required_text(
            values.get("DISPENSER_SIGLENT_IDENTIFIER"),
            name="DISPENSER_SIGLENT_IDENTIFIER",
            maximum_length=512,
        )
        gateway_auth_file = _gateway_auth_file(
            values.get("DISPENSER_SIGLENT_GATEWAY_AUTH_FILE"),
        )
        if values.get("DISPENSER_SIGLENT_VISA_BACKEND") is not None:
            raise ConfigurationError(
                "DISPENSER_SIGLENT_VISA_BACKEND is unsupported by the "
                "gateway-only deployment."
            )
        acceptance_context = _choice(
            values.get("DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT"),
            name="DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT",
            choices=("production_dispenser", "unloaded_hil"),
        )
        state_file_name = "DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE"
        legacy_state_file_name = "DISPENSER_SIGLENT_UNLOADED_HIL_TRIP_LATCH_FILE"
        state_file_value = values.get(state_file_name)
        legacy_state_file_value = values.get(legacy_state_file_name)
        if state_file_value is not None and legacy_state_file_value is not None:
            raise ConfigurationError(
                f"Set only {state_file_name}; do not also set the legacy "
                f"{legacy_state_file_name} alias."
            )
        if state_file_value is None and legacy_state_file_value is not None:
            state_file_name = legacy_state_file_name
            state_file_value = legacy_state_file_value
        unloaded_hil_state_file = _unloaded_hil_state_file(
            state_file_value,
            setting_name=state_file_name,
            acceptance_context=cast(PowerAcceptanceContext, acceptance_context),
        )
        topology = _choice(
            values.get("DISPENSER_SIGLENT_TOPOLOGY"),
            name="DISPENSER_SIGLENT_TOPOLOGY",
            choices=("parallel_ch1",),
        )
        channel = _choice(
            values.get("DISPENSER_SIGLENT_CHANNEL"),
            name="DISPENSER_SIGLENT_CHANNEL",
            choices=("CH1",),
        )
        expected_model = _choice(
            values.get("DISPENSER_SIGLENT_EXPECTED_MODEL"),
            name="DISPENSER_SIGLENT_EXPECTED_MODEL",
            choices=("SPD3303X", "SPD3303X-E", "SPD3303C"),
        )
        expected_serial_number = _required_text(
            values.get("DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER"),
            name="DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER",
            maximum_length=128,
        )
        control_enabled = _required_boolean(
            values.get("DISPENSER_SIGLENT_CONTROL_ENABLED"),
            name="DISPENSER_SIGLENT_CONTROL_ENABLED",
        )
        compliance_voltage_v = _required_number(
            values.get("DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V"),
            name="DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V",
            minimum=0.0,
            maximum=32.0,
        )
        load_factor = 2
        max_load_current_a = _required_number(
            values.get("DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A"),
            name="DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A",
            minimum=0.0,
            maximum=PARALLEL_LOAD_CURRENT_CEILING_A,
            exclusive_minimum=True,
        )
        required_upward_step = PARALLEL_LOAD_UPWARD_STEP_A
        upward_step_a = _required_number(
            values.get("DISPENSER_SIGLENT_UPWARD_STEP_A"),
            name="DISPENSER_SIGLENT_UPWARD_STEP_A",
            minimum=0.0,
            maximum=required_upward_step,
            exclusive_minimum=True,
        )
        timeout_s = _number(
            values.get("DISPENSER_SIGLENT_TIMEOUT_S"),
            name="DISPENSER_SIGLENT_TIMEOUT_S",
            default=DEFAULT_SIGLENT_TIMEOUT_S,
            minimum=0.1,
            maximum=60.0,
        )
        min_command_interval_ms = _number(
            values.get("DISPENSER_SIGLENT_MIN_COMMAND_INTERVAL_MS"),
            name="DISPENSER_SIGLENT_MIN_COMMAND_INTERVAL_MS",
            default=DEFAULT_SIGLENT_COMMAND_INTERVAL_MS,
            minimum=10.0,
            maximum=100.0,
        )
        if expected_model == "SPD3303C":
            raise ConfigurationError(
                "parallel_ch1 is not enabled for the unverified SPD3303C model."
            )
        if not math.isclose(upward_step_a, required_upward_step):
            raise ConfigurationError(
                "DISPENSER_SIGLENT_UPWARD_STEP_A must equal the fixed step for "
                "the selected topology."
            )
        voltage_resolution, current_resolution = {
            "SPD3303X": (0.001, 0.001),
            "SPD3303X-E": (0.01, 0.01),
            "SPD3303C": (0.01, 0.01),
        }[expected_model]
        _require_resolution(
            compliance_voltage_v,
            resolution=voltage_resolution,
            name="DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V",
        )
        _require_resolution(
            max_load_current_a / load_factor,
            resolution=current_resolution,
            name="DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A after topology translation",
        )
        _require_resolution(
            upward_step_a / load_factor,
            resolution=current_resolution,
            name="DISPENSER_SIGLENT_UPWARD_STEP_A after topology translation",
        )

        return cls(
            driver_src=driver_src,
            connection=cast(SiglentConnection, connection),
            identifier=identifier,
            gateway_auth_file=gateway_auth_file,
            acceptance_context=cast(PowerAcceptanceContext, acceptance_context),
            topology=cast(SiglentTopology, topology),
            channel=cast(SiglentChannel, channel),
            expected_model=expected_model,
            expected_serial_number=expected_serial_number,
            compliance_voltage_v=compliance_voltage_v,
            max_load_current_a=max_load_current_a,
            upward_step_a=upward_step_a,
            control_enabled=control_enabled,
            unloaded_hil_state_file=unloaded_hil_state_file,
            timeout_s=timeout_s,
            min_command_interval_ms=min_command_interval_ms,
        )


def _client_file(raw_value: str | None) -> Path:
    if raw_value is None or not raw_value.strip():
        path = DEFAULT_DEVELOPMENT_HICUBE_CLIENT_FILE
    else:
        path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError("DISPENSER_HICUBE_CLIENT_FILE must be absolute.")
    if path.name != "hicube_neo_client.py":
        raise ConfigurationError(
            "DISPENSER_HICUBE_CLIENT_FILE must name hicube_neo_client.py."
        )
    if not path.is_file():
        raise ConfigurationError(
            "DISPENSER_HICUBE_CLIENT_FILE does not identify a readable file."
        )
    return path.resolve()


def _driver_src(raw_value: str | None) -> Path:
    if raw_value is None or not raw_value.strip():
        raise ConfigurationError("DISPENSER_SIGLENT_DRIVER_SRC is required.")
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError("DISPENSER_SIGLENT_DRIVER_SRC must be absolute.")
    package_file = path / "siglent_spd3000" / "__init__.py"
    if not package_file.is_file():
        raise ConfigurationError(
            "DISPENSER_SIGLENT_DRIVER_SRC must contain siglent_spd3000/__init__.py."
        )
    return path.resolve()


def _gateway_auth_file(raw_value: str | None) -> Path:
    if raw_value is None or not raw_value.strip():
        path = DEFAULT_DEVELOPMENT_GATEWAY_AUTH_FILE
    else:
        path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(
            "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE must be absolute."
        )
    if (
        path.name
        not in {
            "gateway-auth.toml",
            "py-siglent-spd3000-gateway-auth.toml",
        }
        or not path.is_file()
    ):
        raise ConfigurationError(
            "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE must identify a readable "
            "supported gateway authentication TOML file."
        )
    return path.resolve()


def _unloaded_hil_state_file(
    raw_value: str | None,
    *,
    setting_name: str,
    acceptance_context: PowerAcceptanceContext,
) -> Path | None:
    if acceptance_context == "production_dispenser":
        if raw_value is not None:
            raise ConfigurationError(
                f"{setting_name} is only valid for the unloaded_hil acceptance context."
            )
        return None
    if raw_value is None or not raw_value.strip():
        raise ConfigurationError(f"{setting_name} is required for unloaded_hil.")
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(f"{setting_name} must be absolute.")
    if path.name in {"", ".", ".."} or path.suffix.lower() != ".json":
        raise ConfigurationError(f"{setting_name} must identify a JSON record file.")
    if not path.parent.is_dir():
        raise ConfigurationError(
            f"{setting_name} must have an existing parent directory."
        )
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ConfigurationError(
            f"{setting_name} must identify a regular non-symlink file."
        )
    return path.resolve()


def _host(raw_value: str | None) -> str:
    if raw_value is None or not raw_value.strip():
        raise ConfigurationError("DISPENSER_HICUBE_HOST is required.")
    host = raw_value.strip()
    if host != raw_value or len(host) > 253:
        raise ConfigurationError("DISPENSER_HICUBE_HOST is invalid.")
    if "://" in host or "/" in host or "\\" in host or any(c.isspace() for c in host):
        raise ConfigurationError(
            "DISPENSER_HICUBE_HOST must be one bare hostname or IP literal."
        )
    if ":" in host:
        try:
            parsed = ipaddress.ip_address(host)
        except ValueError as error:
            raise ConfigurationError(
                "DISPENSER_HICUBE_HOST may not contain an embedded port."
            ) from error
        if parsed.version != 6:
            raise ConfigurationError("DISPENSER_HICUBE_HOST is invalid.")
    return host


def _integer(
    raw_value: str | None,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer.") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _number(
    raw_value: str | None,
    *,
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric.") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _required_number(
    raw_value: str | None,
    *,
    name: str,
    minimum: float,
    maximum: float,
    exclusive_minimum: bool = False,
) -> float:
    if raw_value is None or not raw_value.strip():
        raise ConfigurationError(f"{name} is required.")
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be numeric.") from error
    lower_ok = value > minimum if exclusive_minimum else value >= minimum
    if not math.isfinite(value) or not lower_ok or value > maximum:
        comparator = "greater than" if exclusive_minimum else "at least"
        raise ConfigurationError(
            f"{name} must be finite, {comparator} {minimum}, and at most {maximum}."
        )
    return value


def _required_text(raw_value: str | None, *, name: str, maximum_length: int) -> str:
    if raw_value is None or not raw_value:
        raise ConfigurationError(f"{name} is required.")
    if raw_value != raw_value.strip() or len(raw_value) > maximum_length:
        raise ConfigurationError(f"{name} is invalid.")
    if any(ord(character) < 32 for character in raw_value):
        raise ConfigurationError(f"{name} contains a control character.")
    return raw_value


def _choice(raw_value: str | None, *, name: str, choices: tuple[str, ...]) -> str:
    value = _required_text(raw_value, name=name, maximum_length=64)
    if value not in choices:
        rendered = ", ".join(choices)
        raise ConfigurationError(f"{name} must be one of: {rendered}.")
    return value


def _required_boolean(raw_value: str | None, *, name: str) -> bool:
    value = _required_text(raw_value, name=name, maximum_length=5).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigurationError(f"{name} must be explicitly true or false.")


def _require_resolution(value: float, *, resolution: float, name: str) -> None:
    decimal_value = Decimal(str(value))
    decimal_resolution = Decimal(str(resolution))
    if decimal_value % decimal_resolution != 0:
        raise ConfigurationError(
            f"{name} must align to the expected model resolution {resolution}."
        )
