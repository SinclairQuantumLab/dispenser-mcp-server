from pathlib import Path

import pytest

from dispenser_conditioning_mcp.config import (
    ConfigurationError,
    OperatorConfiguration,
    SourceLayout,
)


def _write_layout(
    tmp_path: Path,
    *,
    main_extra: str = "",
    hicube_extra: str = "",
    gateway_extra: str = "",
    acceptance_context: str = "production_dispenser",
    control_enabled: bool = False,
) -> SourceLayout:
    layout = SourceLayout._for_testing(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "source checkout"
    )
    layout.siglent_settings_directory.mkdir(parents=True)
    layout.hicube_client_file.parent.mkdir(parents=True)
    layout.siglent_driver_src.joinpath("siglent_spd3000").mkdir(parents=True)
    layout.hicube_client_file.write_text("# offline fixture\n", encoding="utf-8")
    layout.siglent_driver_src.joinpath("siglent_spd3000", "__init__.py").write_text(
        "# offline fixture\n", encoding="utf-8"
    )
    layout.siglent_gateway_auth_file.write_text(
        'token = "offline-fixture"\n', encoding="utf-8"
    )
    state_setting = ""
    if acceptance_context == "unloaded_hil":
        state_directory = tmp_path / "protected state"
        state_directory.mkdir()
        state_setting = (
            f'unloaded_hil_state_file = "{state_directory.as_posix()}/state.json"\n'
        )
    layout.mcp_settings_file.write_text(
        (
            "schema_version = 1\n"
            f'acceptance_context = "{acceptance_context}"\n'
            'expected_serial_number = "SPD-OFFLINE"\n'
            "compliance_voltage_v = 10.0\n"
            f"control_enabled = {str(control_enabled).lower()}\n"
            f"{state_setting}{main_extra}"
        ),
        encoding="utf-8",
    )
    layout.hicube_settings_file.write_text(
        (
            "schema_version = 1\n"
            'host = "192.0.2.10"\n'
            "port = 4841\n"
            "timeout_s = 2.5\n"
            f"{hicube_extra}"
        ),
        encoding="utf-8",
    )
    layout.siglent_gateway_settings_file.write_text(
        (
            "schema_version = 1\n"
            'identifier = "offline.test:8765"\n'
            "timeout_s = 2.0\n"
            "minimum_command_interval_ms = 100.0\n"
            f"{gateway_extra}"
        ),
        encoding="utf-8",
    )
    return layout


def test_three_toml_documents_build_one_fixed_policy(tmp_path: Path) -> None:
    layout = _write_layout(tmp_path)

    configuration = OperatorConfiguration.from_toml(layout)

    assert configuration.layout == layout
    assert configuration.hicube.client_file == layout.hicube_client_file.resolve()
    assert configuration.hicube.host == "192.0.2.10"
    assert configuration.hicube.port == 4841
    assert configuration.hicube.timeout_s == 2.5
    assert configuration.siglent.driver_src == layout.siglent_driver_src.resolve()
    assert configuration.siglent.gateway_auth_file == (
        layout.siglent_gateway_auth_file.resolve()
    )
    assert configuration.siglent.connection == "gateway"
    assert configuration.siglent.topology == "parallel_ch1"
    assert configuration.siglent.channel == "CH1"
    assert configuration.siglent.expected_model == "SPD3303X"
    assert configuration.siglent.max_load_current_a == 4.8
    assert configuration.siglent.upward_step_a == 0.2
    assert configuration.siglent.load_current_factor == 2
    assert configuration.siglent.control_enabled is False
    assert configuration.startup.allow_remote_access is False
    assert configuration.startup.port == 8000


def test_safe_defaults_are_loopback_http_and_control_disabled(tmp_path: Path) -> None:
    layout = _write_layout(tmp_path)
    text = layout.mcp_settings_file.read_text(encoding="utf-8")
    text = text.replace("control_enabled = false\n", "")
    layout.mcp_settings_file.write_text(text, encoding="utf-8")

    configuration = OperatorConfiguration.from_toml(layout)

    assert configuration.startup.control_enabled is False
    assert configuration.startup.allow_remote_access is False
    assert configuration.startup.port == 8000


@pytest.mark.parametrize(
    ("file_name", "extra"),
    [
        ("main", "unexpected = true\n"),
        ("hicube", 'client_file = "operator/path.py"\n'),
        ("gateway", 'driver_src = "operator/src"\n'),
    ],
)
def test_each_toml_document_rejects_unknown_keys(
    tmp_path: Path, file_name: str, extra: str
) -> None:
    kwargs = {f"{file_name}_extra": extra}
    layout = _write_layout(tmp_path, **kwargs)  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError, match="unknown setting"):
        OperatorConfiguration.from_toml(layout)


@pytest.mark.parametrize(
    ("key", "replacement", "message"),
    [
        (
            'expected_serial_number = "SPD-OFFLINE"',
            'expected_serial_number = "replace-with-serial"',
            "placeholder",
        ),
        (
            "compliance_voltage_v = 10.0",
            'compliance_voltage_v = "replace-with-voltage"',
            "placeholder",
        ),
        (
            "control_enabled = false",
            'control_enabled = "false"',
            "TOML boolean",
        ),
    ],
)
def test_main_settings_reject_placeholders_and_wrong_types(
    tmp_path: Path, key: str, replacement: str, message: str
) -> None:
    layout = _write_layout(tmp_path)
    text = layout.mcp_settings_file.read_text(encoding="utf-8")
    layout.mcp_settings_file.write_text(
        text.replace(key, replacement), encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match=message):
        OperatorConfiguration.from_toml(layout)


@pytest.mark.parametrize(
    ("path_name", "old", "new", "message"),
    [
        (
            "hicube",
            'host = "192.0.2.10"',
            'host = "replace-with-hicube-host"',
            "placeholder",
        ),
        (
            "gateway",
            'identifier = "offline.test:8765"',
            'identifier = "replace-with-gateway-identifier"',
            "placeholder",
        ),
        ("hicube", "port = 4841", 'port = "4841"', "integer"),
        ("gateway", "timeout_s = 2.0", 'timeout_s = "2.0"', "TOML number"),
    ],
)
def test_device_settings_reject_placeholders_and_wrong_types(
    tmp_path: Path, path_name: str, old: str, new: str, message: str
) -> None:
    layout = _write_layout(tmp_path)
    path = (
        layout.hicube_settings_file
        if path_name == "hicube"
        else layout.siglent_gateway_settings_file
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match=message):
        OperatorConfiguration.from_toml(layout)


def test_unloaded_hil_requires_operator_owned_absolute_state_path(
    tmp_path: Path,
) -> None:
    layout = _write_layout(tmp_path, acceptance_context="unloaded_hil")
    configuration = OperatorConfiguration.from_toml(layout)
    assert configuration.siglent.unloaded_hil_state_file is not None
    assert configuration.siglent.unloaded_hil_state_file.is_absolute()

    text = layout.mcp_settings_file.read_text(encoding="utf-8")
    text = "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith("unloaded_hil_state_file")
    )
    layout.mcp_settings_file.write_text(text + "\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="required for unloaded_hil"):
        OperatorConfiguration.from_toml(layout)


def test_production_context_rejects_unloaded_state_path(tmp_path: Path) -> None:
    layout = _write_layout(
        tmp_path,
        main_extra=f'unloaded_hil_state_file = "{tmp_path.as_posix()}/state.json"\n',
    )
    with pytest.raises(ConfigurationError, match="only valid for unloaded_hil"):
        OperatorConfiguration.from_toml(layout)


@pytest.mark.parametrize("missing", ["hicube", "driver", "auth"])
def test_fixed_repository_owned_paths_fail_closed(tmp_path: Path, missing: str) -> None:
    layout = _write_layout(tmp_path)
    target = {
        "hicube": layout.hicube_client_file,
        "driver": layout.siglent_driver_src / "siglent_spd3000" / "__init__.py",
        "auth": layout.siglent_gateway_auth_file,
    }[missing]
    target.unlink()

    with pytest.raises(ConfigurationError, match="missing"):
        OperatorConfiguration.from_toml(layout)


def test_invalid_toml_error_does_not_disclose_path_or_parser_detail(
    tmp_path: Path,
) -> None:
    layout = _write_layout(tmp_path)
    layout.hicube_settings_file.write_text("[", encoding="utf-8")

    with pytest.raises(ConfigurationError) as captured:
        OperatorConfiguration.from_toml(layout)

    assert str(captured.value) == "HiCube settings file is unreadable or invalid."
    assert str(layout.project_root) not in str(captured.value)


def test_source_layout_is_platform_native_and_has_no_operator_path_inputs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout with spaces"
    layout = SourceLayout._for_testing(  # pyright: ignore[reportPrivateUsage]
        root
    )

    assert layout.mcp_settings_file == root.resolve() / "settings" / "mcp-settings.toml"
    assert layout.hicube_client_file == (
        root.resolve() / "dependencies" / "hicube" / "hicube_neo_client.py"
    )
    assert layout.siglent_driver_src == (
        root.resolve() / "dependencies" / "py-siglent-spd3000" / "src"
    )
    assert layout.siglent_gateway_auth_file == (
        root.resolve() / "settings" / "py-siglent-spd3000" / "gateway-auth.toml"
    )


@pytest.mark.parametrize("value", ['"false"', "0", "1", "[]"])
def test_remote_access_requires_a_toml_boolean(tmp_path: Path, value: str) -> None:
    layout = _write_layout(tmp_path, main_extra=f"allow_remote_access = {value}\n")
    with pytest.raises(ConfigurationError, match="TOML boolean"):
        OperatorConfiguration.from_toml(layout)


@pytest.mark.parametrize("value", ["true", '"8000"', "1023", "65536"])
def test_listener_port_requires_an_unprivileged_integer(
    tmp_path: Path, value: str
) -> None:
    layout = _write_layout(tmp_path, main_extra=f"port = {value}\n")
    with pytest.raises(ConfigurationError, match="port"):
        OperatorConfiguration.from_toml(layout)


@pytest.mark.parametrize(
    "setting",
    [
        'transport = "stdio"',
        'bind_host = "127.0.0.1"',
        'trust_mode = "loopback_only"',
        "allowed_hosts = []",
        "allowed_origins = []",
        'path = "/mcp"',
        "[streamable_http]",
    ],
)
def test_removed_transport_options_are_rejected(tmp_path: Path, setting: str) -> None:
    layout = _write_layout(tmp_path, main_extra=setting + "\n")
    with pytest.raises(ConfigurationError, match="unknown setting"):
        OperatorConfiguration.from_toml(layout)
