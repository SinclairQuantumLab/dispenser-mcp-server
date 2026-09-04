from pathlib import Path

import pytest

import dispenser_conditioning_mcp.config as config_module
from dispenser_conditioning_mcp.config import (
    ConfigurationError,
    HiCubeConfiguration,
    SiglentConfiguration,
)


def test_environment_configuration_is_operator_only(tmp_path: Path) -> None:
    client_file = tmp_path / "hicube_neo_client.py"
    client_file.write_text("# test fixture\n", encoding="utf-8")

    configuration = HiCubeConfiguration.from_environment(
        {
            "DISPENSER_HICUBE_CLIENT_FILE": str(client_file),
            "DISPENSER_HICUBE_HOST": "192.0.2.10",
            "DISPENSER_HICUBE_PORT": "4841",
            "DISPENSER_HICUBE_TIMEOUT_S": "2.5",
        }
    )

    assert configuration.client_file == client_file.resolve()
    assert configuration.host == "192.0.2.10"
    assert configuration.port == 4841
    assert configuration.timeout_s == 2.5


def test_hicube_client_defaults_to_canonical_vendored_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_file = tmp_path / "dependencies/hicube/hicube_neo_client.py"
    client_file.parent.mkdir(parents=True)
    client_file.write_text("# canonical fixture\n", encoding="utf-8")
    monkeypatch.setattr(
        config_module,
        "DEFAULT_DEVELOPMENT_HICUBE_CLIENT_FILE",
        client_file,
    )

    configuration = HiCubeConfiguration.from_environment(
        {"DISPENSER_HICUBE_HOST": "192.0.2.10"}
    )

    assert configuration.client_file == client_file.resolve()


def test_hicube_client_default_fails_closed_when_vendored_file_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_module,
        "DEFAULT_DEVELOPMENT_HICUBE_CLIENT_FILE",
        tmp_path / "dependencies/hicube/hicube_neo_client.py",
    )

    with pytest.raises(ConfigurationError, match="readable file"):
        HiCubeConfiguration.from_environment({"DISPENSER_HICUBE_HOST": "192.0.2.10"})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DISPENSER_HICUBE_HOST", "opc.tcp://192.0.2.10"),
        ("DISPENSER_HICUBE_HOST", "example.test:4840"),
        ("DISPENSER_HICUBE_PORT", "0"),
        ("DISPENSER_HICUBE_TIMEOUT_S", "0"),
    ],
)
def test_environment_configuration_rejects_invalid_values(
    tmp_path: Path, key: str, value: str
) -> None:
    client_file = tmp_path / "hicube_neo_client.py"
    client_file.write_text("# test fixture\n", encoding="utf-8")
    environment = {
        "DISPENSER_HICUBE_CLIENT_FILE": str(client_file),
        "DISPENSER_HICUBE_HOST": "example.test",
        key: value,
    }

    with pytest.raises(ConfigurationError):
        HiCubeConfiguration.from_environment(environment)


def _siglent_environment(tmp_path: Path) -> dict[str, str]:
    driver_src = tmp_path / "driver-src"
    package = driver_src / "siglent_spd3000"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# offline fixture\n", encoding="utf-8")
    auth_file = tmp_path / "gateway-auth.toml"
    auth_file.write_text('token = "offline-test-token"\n', encoding="utf-8")
    return {
        "DISPENSER_SIGLENT_DRIVER_SRC": str(driver_src),
        "DISPENSER_SIGLENT_CONNECTION": "gateway",
        "DISPENSER_SIGLENT_IDENTIFIER": "offline.test:8765",
        "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE": str(auth_file),
        "DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT": "production_dispenser",
        "DISPENSER_SIGLENT_TOPOLOGY": "parallel_ch1",
        "DISPENSER_SIGLENT_CHANNEL": "CH1",
        "DISPENSER_SIGLENT_EXPECTED_MODEL": "SPD3303X",
        "DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER": "SPD-BOUND",
        "DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V": "10.000",
        "DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A": "4.8",
        "DISPENSER_SIGLENT_UPWARD_STEP_A": "0.2",
        "DISPENSER_SIGLENT_CONTROL_ENABLED": "false",
    }


def test_siglent_configuration_requires_explicit_safety_policy(tmp_path: Path) -> None:
    environment = _siglent_environment(tmp_path)
    configuration = SiglentConfiguration.from_environment(environment)

    assert configuration.topology == "parallel_ch1"
    assert configuration.acceptance_context == "production_dispenser"
    assert configuration.channel == "CH1"
    assert configuration.load_current_factor == 2
    assert configuration.expected_serial_number == "SPD-BOUND"
    assert configuration.gateway_auth_file.name == "gateway-auth.toml"
    assert configuration.max_load_current_a == 4.8
    assert configuration.upward_step_a == 0.2
    assert configuration.control_enabled is False
    assert configuration.timeout_s == 5.0
    assert configuration.min_command_interval_ms == 100.0


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DISPENSER_SIGLENT_TOPOLOGY", "independent"),
        ("DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT", "unreviewed_test"),
        ("DISPENSER_SIGLENT_CHANNEL", "CH2"),
        ("DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A", "4.9"),
        ("DISPENSER_SIGLENT_UPWARD_STEP_A", "0.1"),
        ("DISPENSER_SIGLENT_CONTROL_ENABLED", "yes"),
        ("DISPENSER_SIGLENT_EXPECTED_MODEL", "SPD3303C"),
        ("DISPENSER_SIGLENT_VISA_BACKEND", "@py"),
    ],
)
def test_parallel_configuration_rejects_unsafe_values(
    tmp_path: Path, key: str, value: str
) -> None:
    environment = _siglent_environment(tmp_path)
    environment[key] = value

    with pytest.raises(ConfigurationError):
        SiglentConfiguration.from_environment(environment)


def test_siglent_control_flag_is_required(tmp_path: Path) -> None:
    environment = _siglent_environment(tmp_path)
    del environment["DISPENSER_SIGLENT_CONTROL_ENABLED"]

    with pytest.raises(ConfigurationError, match="CONTROL_ENABLED is required"):
        SiglentConfiguration.from_environment(environment)


def test_siglent_acceptance_context_is_explicit_and_allows_unloaded_hil(
    tmp_path: Path,
) -> None:
    environment = _siglent_environment(tmp_path)
    del environment["DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT"]

    with pytest.raises(ConfigurationError, match="ACCEPTANCE_CONTEXT is required"):
        SiglentConfiguration.from_environment(environment)

    environment["DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT"] = "unloaded_hil"
    latch_file = tmp_path / "unloaded-hil-trip.json"
    environment["DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE"] = str(latch_file)
    configuration = SiglentConfiguration.from_environment(environment)
    assert configuration.acceptance_context == "unloaded_hil"
    assert configuration.unloaded_hil_state_file == latch_file.resolve()


def test_unloaded_hil_state_path_is_required_and_context_bound(tmp_path: Path) -> None:
    environment = _siglent_environment(tmp_path)
    environment["DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT"] = "unloaded_hil"

    with pytest.raises(ConfigurationError, match="STATE_FILE is required"):
        SiglentConfiguration.from_environment(environment)

    environment["DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE"] = "relative.json"
    with pytest.raises(ConfigurationError, match="must be absolute"):
        SiglentConfiguration.from_environment(environment)

    environment["DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT"] = "production_dispenser"
    environment["DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE"] = str(
        tmp_path / "trip.json"
    )
    with pytest.raises(ConfigurationError, match="only valid for the unloaded_hil"):
        SiglentConfiguration.from_environment(environment)


def test_legacy_unloaded_hil_trip_latch_path_alias_is_compatible_but_exclusive(
    tmp_path: Path,
) -> None:
    environment = _siglent_environment(tmp_path)
    environment["DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT"] = "unloaded_hil"
    state_file = tmp_path / "unloaded-hil-state.json"
    environment["DISPENSER_SIGLENT_UNLOADED_HIL_TRIP_LATCH_FILE"] = str(state_file)

    configuration = SiglentConfiguration.from_environment(environment)
    assert configuration.unloaded_hil_state_file == state_file.resolve()

    environment["DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE"] = str(state_file)
    with pytest.raises(ConfigurationError, match="Set only.*STATE_FILE"):
        SiglentConfiguration.from_environment(environment)


def test_expected_model_resolution_is_checked_at_startup(tmp_path: Path) -> None:
    environment = _siglent_environment(tmp_path)
    environment.update(
        {
            "DISPENSER_SIGLENT_EXPECTED_MODEL": "SPD3303X-E",
            "DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V": "10.001",
        }
    )

    with pytest.raises(ConfigurationError, match="resolution"):
        SiglentConfiguration.from_environment(environment)


def test_gateway_auth_defaults_to_canonical_development_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _siglent_environment(tmp_path)
    gateway_environment = environment.copy()
    del environment["DISPENSER_SIGLENT_GATEWAY_AUTH_FILE"]
    canonical = tmp_path / "settings/py-siglent-spd3000-gateway-auth.toml"
    canonical.parent.mkdir()
    canonical.write_text('token = "fixture"\n', encoding="utf-8")
    monkeypatch.setattr(
        config_module,
        "DEFAULT_DEVELOPMENT_GATEWAY_AUTH_FILE",
        canonical,
    )

    configuration = SiglentConfiguration.from_environment(environment)
    assert configuration.gateway_auth_file == canonical.resolve()

    gateway_environment["DISPENSER_SIGLENT_CONNECTION"] = "socket"

    with pytest.raises(ConfigurationError, match="must be one of: gateway"):
        SiglentConfiguration.from_environment(gateway_environment)


def test_gateway_auth_default_fails_closed_when_canonical_file_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _siglent_environment(tmp_path)
    del environment["DISPENSER_SIGLENT_GATEWAY_AUTH_FILE"]
    monkeypatch.setattr(
        config_module,
        "DEFAULT_DEVELOPMENT_GATEWAY_AUTH_FILE",
        tmp_path / "settings/py-siglent-spd3000-gateway-auth.toml",
    )

    with pytest.raises(ConfigurationError, match="supported gateway authentication"):
        SiglentConfiguration.from_environment(environment)
