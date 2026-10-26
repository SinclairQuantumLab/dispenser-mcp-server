"""Atomic nonsecret settings owned by the authenticated operator console."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import secrets
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from dispenser_conditioning_mcp.config import (
    DEFAULT_OPC_UA_PORT,
    DEFAULT_SIGLENT_COMMAND_INTERVAL_MS,
    DEFAULT_SIGLENT_TIMEOUT_S,
    DEFAULT_TIMEOUT_S,
    FIXED_SIGLENT_CHANNEL,
    FIXED_SIGLENT_CONNECTION,
    FIXED_SIGLENT_EXPECTED_MODEL,
    FIXED_SIGLENT_TOPOLOGY,
    PARALLEL_LOAD_UPWARD_STEP_A,
    ConfigurationError,
    HiCubeConfiguration,
    LearningModeConfiguration,
    McpStartupConfiguration,
    OperatorConfiguration,
    SiglentConfiguration,
    SourceLayout,
)
from dispenser_conditioning_mcp.current_policy import (
    MAX_CONFIGURABLE_LOAD_CURRENT_A,
)

AuthorityMode = Literal["agent_control", "human_learning"]
BackendKind = Literal["hardware", "simulation"]


def _strict_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a JSON/TOML number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("value must be finite")
    return number


StrictNumber = Annotated[float, BeforeValidator(_strict_number)]


def _plain_text(value: str, *, field: str, maximum: int) -> str:
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
        or (value.startswith("<") and value.endswith(">"))
    ):
        raise ValueError(f"{field} is missing, contains a placeholder, or is invalid")
    return value


class HardwareConsoleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    hicube_host: str = ""
    hicube_port: int = Field(default=DEFAULT_OPC_UA_PORT, ge=1, le=65535)
    hicube_timeout_s: StrictNumber = Field(default=DEFAULT_TIMEOUT_S, ge=0.1, le=60.0)
    siglent_identifier: str = ""
    siglent_timeout_s: StrictNumber = Field(
        default=DEFAULT_SIGLENT_TIMEOUT_S, ge=0.1, le=60.0
    )
    siglent_minimum_command_interval_ms: StrictNumber = Field(
        default=DEFAULT_SIGLENT_COMMAND_INTERVAL_MS, ge=10.0, le=100.0
    )

    @field_validator("hicube_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if value == "":
            return value
        value = _plain_text(value, field="hicube_host", maximum=253)
        if (
            "://" in value
            or "/" in value
            or "\\" in value
            or any(character.isspace() for character in value)
        ):
            raise ValueError("hicube_host must be one bare hostname or IP literal")
        if ":" in value:
            try:
                if ipaddress.ip_address(value).version != 6:
                    raise ValueError("hicube_host is invalid")
            except ValueError as error:
                raise ValueError(
                    "hicube_host may not contain an embedded port"
                ) from error
        return value

    @field_validator("siglent_identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if value == "":
            return value
        return _plain_text(value, field="siglent_identifier", maximum=512)


class SimulationConsoleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    seed: str
    scenario: str

    @field_validator("seed")
    @classmethod
    def validate_seed(cls, value: str) -> str:
        return _plain_text(value, field="simulation.seed", maximum=256)

    @field_validator("scenario")
    @classmethod
    def validate_scenario(cls, value: str) -> str:
        return _plain_text(value, field="simulation.scenario", maximum=128)


class OperatorConsoleSettings(BaseModel):
    """One complete nonsecret run candidate; credentials remain in the fixed file."""

    model_config = ConfigDict(extra="forbid", strict=True)

    backend: BackendKind
    authority_mode: AuthorityMode = "agent_control"
    control_enabled: bool = False
    acceptance_context: Literal["production_dispenser", "no_load_test"]
    expected_serial_number: str = ""
    compliance_voltage_v: StrictNumber = Field(ge=0.001, le=32.0)
    max_load_current_A: StrictNumber = Field(gt=0.0, le=MAX_CONFIGURABLE_LOAD_CURRENT_A)
    sample_interval_s: StrictNumber = Field(default=5.0, ge=0.1, le=3600.0)
    hardware: HardwareConsoleSettings = Field(default_factory=HardwareConsoleSettings)
    simulation: SimulationConsoleSettings

    @field_validator("expected_serial_number")
    @classmethod
    def validate_serial(cls, value: str) -> str:
        if value == "":
            return value
        return _plain_text(value, field="expected_serial_number", maximum=128)

    @field_validator("compliance_voltage_v")
    @classmethod
    def validate_voltage_resolution(cls, value: float) -> float:
        if not math.isclose(value * 1000.0, round(value * 1000.0), abs_tol=1e-9):
            raise ValueError("compliance_voltage_v must align to 0.001 V")
        return value

    @model_validator(mode="after")
    def require_selected_backend_fields(self) -> OperatorConsoleSettings:
        if self.backend == "hardware":
            _plain_text(
                self.expected_serial_number,
                field="expected_serial_number",
                maximum=128,
            )
            _plain_text(
                self.hardware.hicube_host,
                field="hardware.hicube_host",
                maximum=253,
            )
            _plain_text(
                self.hardware.siglent_identifier,
                field="hardware.siglent_identifier",
                maximum=512,
            )
        return self

    @property
    def learning(self) -> LearningModeConfiguration:
        return LearningModeConfiguration(
            enabled=True,
            sample_interval_s=self.sample_interval_s,
            human_control_only=self.authority_mode == "human_learning",
        )

    def hardware_configuration(self, layout: SourceLayout) -> OperatorConfiguration:
        client_file = layout.hicube_client_file
        driver_src = layout.siglent_driver_src
        auth_file = layout.siglent_gateway_auth_file
        if not client_file.is_file():
            raise ConfigurationError("The repository-owned HiCube client is missing.")
        if not (driver_src / "siglent_spd3000" / "__init__.py").is_file():
            raise ConfigurationError("The Siglent driver submodule is missing.")
        if not auth_file.is_file():
            raise ConfigurationError(
                "The operator-owned Siglent gateway authentication file is missing."
            )
        startup = McpStartupConfiguration(
            acceptance_context=self.acceptance_context,
            expected_serial_number=self.expected_serial_number,
            compliance_voltage_v=self.compliance_voltage_v,
            control_enabled=self.control_enabled,
            allow_remote_access=False,
            port=8000,
            max_load_current_A=self.max_load_current_A,
            learning=self.learning,
        )
        hicube = HiCubeConfiguration(
            client_file=client_file.resolve(),
            host=self.hardware.hicube_host,
            port=self.hardware.hicube_port,
            timeout_s=self.hardware.hicube_timeout_s,
        )
        siglent = SiglentConfiguration(
            driver_src=driver_src.resolve(),
            connection=cast(Any, FIXED_SIGLENT_CONNECTION),
            identifier=self.hardware.siglent_identifier,
            gateway_auth_file=auth_file.resolve(),
            acceptance_context=self.acceptance_context,
            topology=cast(Any, FIXED_SIGLENT_TOPOLOGY),
            channel=cast(Any, FIXED_SIGLENT_CHANNEL),
            expected_model=FIXED_SIGLENT_EXPECTED_MODEL,
            expected_serial_number=self.expected_serial_number,
            compliance_voltage_v=self.compliance_voltage_v,
            max_load_current_a=self.max_load_current_A,
            upward_step_a=PARALLEL_LOAD_UPWARD_STEP_A,
            control_enabled=self.control_enabled,
            timeout_s=self.hardware.siglent_timeout_s,
            min_command_interval_ms=(self.hardware.siglent_minimum_command_interval_ms),
        )
        return OperatorConfiguration(
            layout=layout, startup=startup, hicube=hicube, siglent=siglent
        )

    def simulator_configuration(self) -> Any:
        from dispenser_simulator.model import HiddenSimulatorConfig

        try:
            return HiddenSimulatorConfig(
                seed=self.simulation.seed,
                scenario=self.simulation.scenario,
                acceptance_context=self.acceptance_context,
                control_enabled=self.control_enabled,
                compliance_voltage_v=self.compliance_voltage_v,
                max_load_current_a=self.max_load_current_A,
            )
        except ValueError as error:
            raise ConfigurationError("Simulation settings are invalid.") from error


class OperatorSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: str | None = Field(default=None, min_length=1, max_length=128)
    settings: OperatorConsoleSettings


class OperatorSettingsStore:
    """Single-file atomic persistence for nonsecret console settings."""

    def __init__(self, layout: SourceLayout) -> None:
        self.layout = layout
        self.path = layout.settings_directory / "console-settings.toml"

    @staticmethod
    def absent_revision() -> str:
        return "absent"

    def load(self) -> tuple[OperatorConsoleSettings | None, str]:
        if not self.path.exists():
            return None, self.absent_revision()
        try:
            with self.path.open("rb") as stream:
                raw: object = tomllib.load(stream)
            settings = OperatorConsoleSettings.model_validate(raw)
        except Exception as error:
            raise ConfigurationError(
                "Operator console settings are unreadable or invalid."
            ) from error
        return settings, self._revision(settings)

    def save(
        self, update: OperatorSettingsUpdate, *, active: bool
    ) -> tuple[OperatorConsoleSettings, str]:
        if active:
            raise ConfigurationError(
                "Stop the active run before replacing run or backend settings."
            )
        _, current_revision = self.load()
        expected_revision = update.expected_revision or self.absent_revision()
        if expected_revision != current_revision:
            raise ConfigurationError(
                "Operator settings changed since they were read; reload before saving."
            )
        return self._write(update.settings)

    def save_run_policy(
        self,
        *,
        expected_revision: str,
        authority_mode: AuthorityMode | None = None,
        sample_interval_s: float | None = None,
    ) -> tuple[OperatorConsoleSettings, str]:
        current, current_revision = self.load()
        if current is None or current_revision != expected_revision:
            raise ConfigurationError(
                "Operator settings changed since the run started; reload before changing run policy."
            )
        update = current.model_copy(
            update={
                **(
                    {"authority_mode": authority_mode}
                    if authority_mode is not None
                    else {}
                ),
                **(
                    {"sample_interval_s": sample_interval_s}
                    if sample_interval_s is not None
                    else {}
                ),
            }
        )
        return self._write(OperatorConsoleSettings.model_validate(update))

    def suggested(self) -> dict[str, Any]:
        """Derive unsaved UI defaults from supported source settings when possible."""

        suggested = self._suggested_from_split_documents()
        if suggested is not None:
            return suggested.model_dump(mode="json")
        return {
            "backend": "hardware",
            "authority_mode": "human_learning",
            "control_enabled": False,
            "acceptance_context": "production_dispenser",
            "expected_serial_number": "",
            "compliance_voltage_v": 1.0,
            "max_load_current_A": 4.8,
            "sample_interval_s": 5.0,
            "hardware": {
                "hicube_host": "",
                "hicube_port": DEFAULT_OPC_UA_PORT,
                "hicube_timeout_s": DEFAULT_TIMEOUT_S,
                "siglent_identifier": "",
                "siglent_timeout_s": DEFAULT_SIGLENT_TIMEOUT_S,
                "siglent_minimum_command_interval_ms": DEFAULT_SIGLENT_COMMAND_INTERVAL_MS,
            },
            "simulation": {
                "seed": "operator-console-default",
                "scenario": "nominal_recovery",
            },
        }

    def _write(
        self, settings: OperatorConsoleSettings
    ) -> tuple[OperatorConsoleSettings, str]:
        rendered = _render_toml(settings).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            _fsync_parent(self.path.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return settings, self._revision(settings)

    def auth_present(self) -> bool:
        return self.layout.siglent_gateway_auth_file.is_file()

    @staticmethod
    def _revision(settings: OperatorConsoleSettings) -> str:
        canonical = json.dumps(
            settings.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _suggested_from_split_documents(self) -> OperatorConsoleSettings | None:
        paths = (
            self.layout.mcp_settings_file,
            self.layout.hicube_settings_file,
            self.layout.siglent_gateway_settings_file,
        )
        if not all(path.is_file() for path in paths):
            return None
        try:
            documents: list[dict[str, object]] = []
            for path in paths:
                with path.open("rb") as stream:
                    documents.append(tomllib.load(stream))
            main, hicube, siglent = documents
            simulation_value = main.get("simulation", {})
            if not isinstance(simulation_value, dict):
                return None
            simulation = cast(dict[str, object], simulation_value)
            learning_value = main.get("learning", {})
            if not isinstance(learning_value, dict):
                return None
            learning = cast(dict[str, object], learning_value)
            return OperatorConsoleSettings.model_validate(
                {
                    "backend": main.get("backend", "hardware"),
                    "authority_mode": (
                        "human_learning"
                        if learning.get("enabled", False)
                        else "agent_control"
                    ),
                    "control_enabled": main.get(
                        "control_enabled", simulation.get("control_enabled", False)
                    ),
                    "acceptance_context": main.get(
                        "acceptance_context", "production_dispenser"
                    ),
                    "expected_serial_number": main.get(
                        "expected_serial_number", "SIMULATED-NOT-HARDWARE"
                    ),
                    "compliance_voltage_v": main.get(
                        "compliance_voltage_v",
                        simulation.get("compliance_voltage_v", 1.0),
                    ),
                    "max_load_current_A": main.get("max_load_current_A", 4.8),
                    "sample_interval_s": learning.get("sample_interval_s", 5.0),
                    "hardware": {
                        "hicube_host": hicube.get("host"),
                        "hicube_port": hicube.get("port", DEFAULT_OPC_UA_PORT),
                        "hicube_timeout_s": hicube.get("timeout_s", DEFAULT_TIMEOUT_S),
                        "siglent_identifier": siglent.get("identifier"),
                        "siglent_timeout_s": siglent.get(
                            "timeout_s", DEFAULT_SIGLENT_TIMEOUT_S
                        ),
                        "siglent_minimum_command_interval_ms": siglent.get(
                            "minimum_command_interval_ms",
                            DEFAULT_SIGLENT_COMMAND_INTERVAL_MS,
                        ),
                    },
                    "simulation": {
                        "seed": simulation.get("seed", "operator-console-default"),
                        "scenario": simulation.get("scenario", "nominal_recovery"),
                    },
                }
            )
        except Exception:
            return None


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_toml(settings: OperatorConsoleSettings) -> str:
    value = settings.model_dump(mode="json")
    hardware = cast(dict[str, Any], value.pop("hardware"))
    simulation = cast(dict[str, Any], value.pop("simulation"))
    lines = [
        "# Managed atomically by the authenticated operator console.\n",
        f"backend = {_quote(value['backend'])}\n",
        f"authority_mode = {_quote(value['authority_mode'])}\n",
        f"control_enabled = {str(value['control_enabled']).lower()}\n",
        f"acceptance_context = {_quote(value['acceptance_context'])}\n",
        f"expected_serial_number = {_quote(value['expected_serial_number'])}\n",
        f"compliance_voltage_v = {value['compliance_voltage_v']}\n",
        f"max_load_current_A = {value['max_load_current_A']}\n",
        f"sample_interval_s = {value['sample_interval_s']}\n",
        "\n[hardware]\n",
        f"hicube_host = {_quote(hardware['hicube_host'])}\n",
        f"hicube_port = {hardware['hicube_port']}\n",
        f"hicube_timeout_s = {hardware['hicube_timeout_s']}\n",
        f"siglent_identifier = {_quote(hardware['siglent_identifier'])}\n",
        f"siglent_timeout_s = {hardware['siglent_timeout_s']}\n",
        "siglent_minimum_command_interval_ms = "
        f"{hardware['siglent_minimum_command_interval_ms']}\n",
        "\n[simulation]\n",
        f"seed = {_quote(simulation['seed'])}\n",
        f"scenario = {_quote(simulation['scenario'])}\n",
    ]
    return "".join(lines)


def _fsync_parent(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
