from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dispenser_conditioning_mcp import __main__, app, backend, simulation_app
from dispenser_conditioning_mcp.config import OperatorConfiguration
from dispenser_conditioning_mcp.domain import RawPressureObservation
from dispenser_conditioning_mcp.startup_check import check_connections


@pytest.mark.parametrize("failure", [None, "G1", "PSU"])
def test_reads_fail_fast_preserve_cause(failure, capsys):
    pressure = Mock(spec=["read"])
    power = Mock(spec=["read_state"])
    pressure.read.return_value = RawPressureObservation(
        datetime.now(UTC), 1.33e-7, "TC"
    )
    power.read_state.return_value = SimpleNamespace(
        manufacturer="SIGLENT",
        model="SPD3303X",
        serial_number="fixture",
        output_enabled=False,
        commanded_load_current_limit_a=0,
        measured_native_channel_current_a=0,
    )
    if failure:
        try:
            raise ValueError("fixture cause")
        except ValueError as cause:
            error = RuntimeError("fixture wrapper")
            error.__cause__ = cause
        (pressure.read if failure == "G1" else power.read_state).side_effect = error
    if failure:
        with pytest.raises(RuntimeError) as caught:
            check_connections(pressure, power)
        assert caught.value is error
        assert isinstance(caught.value.__cause__, ValueError)
    else:
        check_connections(pressure, power)
    pressure.read.assert_called_once_with()
    if failure == "G1":
        power.read_state.assert_not_called()
    else:
        power.read_state.assert_called_once_with()
    output = capsys.readouterr().err
    if failure:
        assert f"FAIL [{failure}]" in output
        assert "aborting HTTP startup" in output
    else:
        assert "G1=1.33e-07 mbar" in output and "output=OFF" in output


def test_cli_enables_checks_and_continues_to_listener(monkeypatch):
    startup = Mock(return_value=("server", "transport"))
    listener = Mock()
    monkeypatch.setattr(__main__, "create_startup_server", startup)
    monkeypatch.setattr(__main__, "run_configured_transport", listener)
    __main__.main()
    startup.assert_called_once_with(check_hardware=True)
    listener.assert_called_once_with("server", "transport")


def test_only_explicit_hardware_construction_reads(monkeypatch):
    pressure, power = (
        Mock(spec=["read"]),
        Mock(spec=["read_state", "reload_current_limit"]),
    )
    pressure.read.side_effect = OSError("fixture")
    power.read_state.side_effect = OSError("fixture")
    monkeypatch.setattr(app, "HiCubeNeoPressureSource", lambda **kw: pressure)
    monkeypatch.setattr(app, "DispenserPowerController", lambda config: power)
    monkeypatch.setattr(app, "create_server", lambda *args, **kw: "server")
    config = SimpleNamespace(
        hicube=SimpleNamespace(client_file=None, host="fake", port=1, timeout_s=1),
        siglent=None,
        startup=SimpleNamespace(max_load_current_A=4.8),
    )
    assert app.create_configured_server(config) == "server"
    pressure.read.assert_not_called()
    with pytest.raises(OSError):
        app.create_configured_server(config, check_hardware=True)
    pressure.read.assert_called_once()
    power.read_state.assert_not_called()


def test_simulation_skips_hardware_checks(tmp_path, monkeypatch):
    settings = tmp_path / "settings.toml"
    settings.write_text('backend = "simulation"\n')
    monkeypatch.setattr(
        simulation_app, "create_simulation_server", lambda *args, **kw: "sim"
    )

    def forbidden(*args, **kwargs):
        pytest.fail("hardware configuration accessed")

    monkeypatch.setattr(OperatorConfiguration, "from_toml", forbidden)
    server, _ = backend.create_startup_server(
        SimpleNamespace(mcp_settings_file=settings), check_hardware=True
    )
    assert server == "sim"


def test_cli_failure_does_not_start_listener(monkeypatch):
    failure = RuntimeError("fixture")
    monkeypatch.setattr(__main__, "create_startup_server", Mock(side_effect=failure))
    listener = Mock()
    monkeypatch.setattr(__main__, "run_configured_transport", listener)
    with pytest.raises(RuntimeError) as caught:
        __main__.main()
    assert caught.value is failure
    listener.assert_not_called()


def test_factory_closes_device_if_wrapper_setup_fails(monkeypatch):
    from dispenser_conditioning_mcp import siglent

    device = Mock(spec=["close"])
    module = SimpleNamespace(
        load_gateway_auth=lambda *a, **kw: "fixture",
        SPD3000=SimpleNamespace(connect=lambda *a, **kw: device),
    )
    monkeypatch.setattr(siglent, "_load_driver_module", lambda path: module)
    config = SimpleNamespace(
        driver_src=None,
        timeout_s=1,
        min_command_interval_ms=1,
        gateway_auth_file=None,
        connection="gateway",
        identifier="fixture",
        channel="CH1",
    )
    with pytest.raises(AttributeError):
        siglent.SiglentDriverSessionFactory(config)()
    device.close.assert_called_once_with()
