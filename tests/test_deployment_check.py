from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dispenser_conditioning_mcp import deployment_check
from dispenser_conditioning_mcp.config import ConfigurationError


def _raise(error: BaseException) -> None:
    raise error


def _install_successful_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Any:
    operator = SimpleNamespace(
        startup=object(),
        hicube=SimpleNamespace(
            client_file=tmp_path / "hicube_neo_client.py",
            host="offline.invalid",
            port=4840,
            timeout_s=1.0,
        ),
        siglent=SimpleNamespace(
            driver_src=tmp_path / "driver-src",
            gateway_auth_file=tmp_path / "gateway-auth.toml",
        ),
    )

    def load_operator() -> Any:
        return operator

    def build_transport(_settings: object) -> object:
        return object()

    def accept_one_argument(_value: object) -> None:
        return None

    def build_pressure_source(**_kwargs: object) -> object:
        return object()

    def build_controller(_configuration: object) -> object:
        return object()

    def build_server(_source: object, _controller: object) -> object:
        return object()

    monkeypatch.setattr(
        deployment_check.OperatorConfiguration,
        "from_toml",
        staticmethod(load_operator),
    )
    monkeypatch.setattr(
        deployment_check.McpTransportConfiguration,
        "from_settings",
        staticmethod(build_transport),
    )
    monkeypatch.setattr(
        deployment_check,
        "validate_hicube_client_installation",
        accept_one_argument,
    )
    monkeypatch.setattr(
        deployment_check,
        "validate_siglent_driver_installation",
        accept_one_argument,
    )
    monkeypatch.setattr(
        deployment_check,
        "_validate_auth_access",
        accept_one_argument,
    )
    monkeypatch.setattr(
        deployment_check,
        "HiCubeNeoPressureSource",
        build_pressure_source,
    )
    monkeypatch.setattr(
        deployment_check,
        "DispenserPowerController",
        build_controller,
    )
    monkeypatch.setattr(
        deployment_check,
        "create_server",
        build_server,
    )
    return operator


def _replace_stage_with_failure(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    error: BaseException,
) -> None:
    if stage == "CONFIG":

        def fail_config() -> None:
            _raise(error)

        monkeypatch.setattr(
            deployment_check.OperatorConfiguration,
            "from_toml",
            staticmethod(fail_config),
        )
    elif stage == "TRANSPORT_POLICY":

        def fail_transport(_settings: object) -> None:
            _raise(error)

        monkeypatch.setattr(
            deployment_check.McpTransportConfiguration,
            "from_settings",
            staticmethod(fail_transport),
        )
    elif stage == "HICUBE_IMPORT":

        def fail_hicube(_path: object) -> None:
            _raise(error)

        monkeypatch.setattr(
            deployment_check,
            "validate_hicube_client_installation",
            fail_hicube,
        )
    elif stage == "SIGLENT_IMPORT":

        def fail_siglent(_path: object) -> None:
            _raise(error)

        monkeypatch.setattr(
            deployment_check,
            "validate_siglent_driver_installation",
            fail_siglent,
        )
    elif stage == "AUTH_ACCESS":

        def fail_auth(_operator: object) -> None:
            _raise(error)

        monkeypatch.setattr(
            deployment_check,
            "_validate_auth_access",
            fail_auth,
        )
    elif stage == "SERVER_ASSEMBLY":

        def fail_server(_source: object, _controller: object) -> None:
            _raise(error)

        monkeypatch.setattr(
            deployment_check,
            "create_server",
            fail_server,
        )
    else:  # pragma: no cover - test helper invariant
        raise AssertionError(f"Unknown test stage: {stage}")


@pytest.mark.parametrize(
    ("stage", "safe_message"),
    [
        ("CONFIG", "Operator settings could not be loaded safely."),
        ("TRANSPORT_POLICY", "Startup transport policy could not be validated."),
        (
            "HICUBE_IMPORT",
            "The vendored HiCube client or an installed dependency could not be imported.",
        ),
        (
            "SIGLENT_IMPORT",
            "The pinned Siglent source, import origin, or required public API is invalid.",
        ),
        (
            "AUTH_ACCESS",
            "The gateway authentication file is not readable by this process identity.",
        ),
        ("SERVER_ASSEMBLY", "Offline MCP and controller assembly failed."),
    ],
)
def test_each_failure_stage_has_a_stable_safe_default_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
    safe_message: str,
) -> None:
    _install_successful_stages(monkeypatch, tmp_path)
    _replace_stage_with_failure(
        monkeypatch,
        stage,
        RuntimeError("token=do-not-print endpoint=do-not-print /protected/path"),
    )

    with pytest.raises(SystemExit) as captured_exit:
        deployment_check.main([])

    output = capsys.readouterr()
    assert captured_exit.value.code == 2
    assert captured_exit.value.__cause__ is None
    assert output.out == ""
    assert output.err == (
        f"Offline deployment validation failed [{stage}]: {safe_message}\n"
    )
    assert "do-not-print" not in output.err
    assert "/protected/path" not in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("stage", ["CONFIG", "TRANSPORT_POLICY"])
def test_configuration_error_message_is_allowlisted_for_operator_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    _install_successful_stages(monkeypatch, tmp_path)
    error = ConfigurationError("compliance_voltage_v must be a TOML number.")
    _replace_stage_with_failure(monkeypatch, stage, error)

    with pytest.raises(SystemExit) as captured_exit:
        deployment_check.main([])

    output = capsys.readouterr()
    assert captured_exit.value.code == 2
    assert output.out == ""
    assert output.err == (
        f"Offline deployment validation failed [{stage}]: "
        "compliance_voltage_v must be a TOML number.\n"
    )


def test_diagnostic_adds_only_exception_class_and_redacts_raw_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_successful_stages(monkeypatch, tmp_path)
    _replace_stage_with_failure(
        monkeypatch,
        "AUTH_ACCESS",
        OSError("token=secret-value host=secret-host /secret/path"),
    )

    with pytest.raises(SystemExit) as captured_exit:
        deployment_check.main(["--diagnostic"])

    output = capsys.readouterr()
    assert captured_exit.value.code == 2
    assert output.out == ""
    assert output.err == (
        "Offline deployment validation failed [AUTH_ACCESS]: The gateway "
        "authentication file is not readable by this process identity. "
        "exception=OSError\n"
    )
    assert "secret-value" not in output.err
    assert "secret-host" not in output.err
    assert "/secret/path" not in output.err
    assert "Traceback" not in output.err


def test_success_writes_only_the_existing_stdout_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operator = _install_successful_stages(monkeypatch, tmp_path)
    transport_settings: list[object] = []

    def record_transport(settings: object) -> None:
        transport_settings.append(settings)

    monkeypatch.setattr(
        deployment_check.McpTransportConfiguration,
        "from_settings",
        staticmethod(record_transport),
    )

    deployment_check.main([])

    output = capsys.readouterr()
    assert output.out == "Offline deployment validation passed.\n"
    assert output.err == ""
    assert transport_settings == [operator.startup]


@pytest.mark.parametrize("argument", ["--token=do-not-print", "--diag"])
def test_unknown_option_is_rejected_without_echoing_its_value_or_running_checks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argument: str,
) -> None:
    calls: list[str] = []

    def record_configuration_call() -> None:
        calls.append("CONFIG")

    monkeypatch.setattr(
        deployment_check.OperatorConfiguration,
        "from_toml",
        staticmethod(record_configuration_call),
    )
    with pytest.raises(SystemExit) as captured_exit:
        deployment_check.main([argument])

    output = capsys.readouterr()
    assert captured_exit.value.code == 2
    assert output.out == ""
    assert output.err == (
        "python -m dispenser_conditioning_mcp.deployment_check: error: "
        "unsupported command-line option.\n"
    )
    assert "do-not-print" not in output.err
    assert "--diag" not in output.err
    assert calls == []


def test_transport_policy_failure_stops_before_import_auth_and_assembly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operator = _install_successful_stages(monkeypatch, tmp_path)
    events: list[str] = []

    def load_operator() -> Any:
        events.append("CONFIG")
        return operator

    def fail_transport(_settings: object) -> None:
        events.append("TRANSPORT_POLICY")
        raise ConfigurationError("streamable_http.path is invalid.")

    def forbidden_stage(_value: object) -> None:
        events.append("FORBIDDEN")

    monkeypatch.setattr(
        deployment_check.OperatorConfiguration,
        "from_toml",
        staticmethod(load_operator),
    )
    monkeypatch.setattr(
        deployment_check.McpTransportConfiguration,
        "from_settings",
        staticmethod(fail_transport),
    )
    monkeypatch.setattr(
        deployment_check,
        "validate_hicube_client_installation",
        forbidden_stage,
    )
    monkeypatch.setattr(
        deployment_check,
        "validate_siglent_driver_installation",
        forbidden_stage,
    )
    monkeypatch.setattr(
        deployment_check,
        "_validate_auth_access",
        forbidden_stage,
    )

    with pytest.raises(SystemExit) as captured_exit:
        deployment_check.main([])

    output = capsys.readouterr()
    assert captured_exit.value.code == 2
    assert events == ["CONFIG", "TRANSPORT_POLICY"]
    assert "streamable_http.path is invalid." in output.err


def test_auth_access_opens_but_does_not_read_the_file() -> None:
    events: list[str] = []

    class OpenOnlyFile:
        def __enter__(self) -> OpenOnlyFile:
            events.append("enter")
            return self

        def __exit__(
            self,
            _exception_type: object,
            _exception: object,
            _traceback: object,
        ) -> None:
            events.append("exit")

    class OpenOnlyPath:
        def open(self, mode: str) -> OpenOnlyFile:
            events.append(mode)
            return OpenOnlyFile()

    operator = SimpleNamespace(
        siglent=SimpleNamespace(gateway_auth_file=OpenOnlyPath())
    )

    deployment_check._validate_auth_access(operator)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    assert events == ["rb", "enter", "exit"]
