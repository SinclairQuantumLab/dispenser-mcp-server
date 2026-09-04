from __future__ import annotations

import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from dispenser_conditioning_mcp import siglent
from dispenser_conditioning_mcp.config import SiglentConfiguration
from dispenser_conditioning_mcp.interlock import FileUnloadedHilDurableStateProvider
from dispenser_conditioning_mcp.power_domain import (
    DeviceIdentity,
    EnableConfirmation,
    NativeChannel,
    PowerAcceptanceContext,
    PowerControlError,
    PowerMutationOperation,
    RawChannelState,
    UnloadedHilDurableState,
    UnloadedHilDurableStateProvider,
    UnloadedHilPendingOperationRecord,
    UnloadedHilTripRecord,
)
from dispenser_conditioning_mcp.siglent import (
    DispenserPowerController,
    SiglentDriverSession,
    SiglentDriverSessionFactory,
)


class FakeDevice:
    def __init__(self) -> None:
        self.identity = DeviceIdentity(
            manufacturer="Siglent Technologies",
            model="SPD3303X",
            serial_number="SPD-BOUND",
            firmware_version="1.0",
        )
        self.operating_mode = "parallel"
        self.voltage_setpoint_v = 10.0
        self.current_setpoint_a = 0.0
        self.measured_voltage_v = 9.9
        self.measured_current_a = 0.0
        self.measured_power_w: float | None = 0.0
        self.output_enabled = False
        self.ch2_output_enabled = False
        self.ch2_current_setpoint_a = 0.0
        self.regulation_mode = "CC"
        self.events: list[str] = []
        self.fail_next_state = False
        self.fail_output_off_channels: set[str] = set()
        self.enable_current_drift_a: float | None = None
        self.enable_voltage_drift_v: float | None = None
        self.measured_current_read_sequence: list[float | Exception] = []


class FakeSession:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device

    def read_identity(self) -> DeviceIdentity:
        self.device.events.append("read_identity")
        return self.device.identity

    def read_channel_state(self) -> RawChannelState:
        self.device.events.append("read_state")
        if self.device.fail_next_state:
            self.device.fail_next_state = False
            raise RuntimeError("TCPIP::secret-internal-source")
        return RawChannelState(
            operating_mode=self.device.operating_mode,
            voltage_setpoint_v=self.device.voltage_setpoint_v,
            current_setpoint_a=self.device.current_setpoint_a,
            measured_voltage_v=self.device.measured_voltage_v,
            measured_current_a=self.device.measured_current_a,
            measured_power_w=self.device.measured_power_w,
            output_enabled=self.device.output_enabled,
            regulation_mode=self.device.regulation_mode,
        )

    def read_output_enabled(self) -> bool:
        self.device.events.append("read_output")
        return self.device.output_enabled

    def read_current_setpoint_a(self) -> float:
        self.device.events.append("read_current")
        return self.device.current_setpoint_a

    def read_measured_current_a(self) -> float:
        self.device.events.append("read_measured_current")
        if self.device.measured_current_read_sequence:
            result = self.device.measured_current_read_sequence.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return self.device.measured_current_a

    def read_channel_output_enabled(self, channel: NativeChannel) -> bool:
        self.device.events.append(f"read_channel_output:{channel}")
        return (
            self.device.output_enabled
            if channel == "CH1"
            else self.device.ch2_output_enabled
        )

    def read_channel_current_setpoint_a(self, channel: NativeChannel) -> float:
        self.device.events.append(f"read_channel_current:{channel}")
        return (
            self.device.current_setpoint_a
            if channel == "CH1"
            else self.device.ch2_current_setpoint_a
        )

    def set_voltage_v(self, value: float) -> None:
        self.device.events.append(f"set_voltage:{value}")
        self.device.voltage_setpoint_v = value

    def set_current_a(self, value: float) -> None:
        self.device.events.append(f"set_current:{value}")
        self.device.current_setpoint_a = value

    def set_channel_current_a(self, channel: NativeChannel, value: float) -> None:
        self.device.events.append(f"set_channel_current:{channel}:{value}")
        if channel == "CH1":
            self.device.current_setpoint_a = value
        else:
            self.device.ch2_current_setpoint_a = value

    def set_output_enabled(self, enabled: bool) -> None:
        self.device.events.append(f"set_output:{enabled}")
        if not enabled and "CH1" in self.device.fail_output_off_channels:
            raise RuntimeError("private output failure")
        self.device.output_enabled = enabled
        if enabled and self.device.enable_current_drift_a is not None:
            self.device.current_setpoint_a = self.device.enable_current_drift_a
        if enabled and self.device.enable_voltage_drift_v is not None:
            self.device.voltage_setpoint_v = self.device.enable_voltage_drift_v

    def set_channel_output_enabled(self, channel: NativeChannel, enabled: bool) -> None:
        self.device.events.append(f"set_channel_output:{channel}:{enabled}")
        if not enabled and channel in self.device.fail_output_off_channels:
            raise RuntimeError("private output failure")
        if channel == "CH1":
            self.device.output_enabled = enabled
        else:
            self.device.ch2_output_enabled = enabled

    @contextmanager
    def atomic_write_batch(self) -> Generator[None]:
        yield

    def close(self) -> None:
        self.device.events.append("close")


class FakeSessionFactory:
    def __init__(self, device: FakeDevice) -> None:
        self.device = device
        self.calls = 0

    def __call__(self) -> FakeSession:
        self.calls += 1
        return FakeSession(self.device)


def configuration(
    tmp_path: Path,
    *,
    control_enabled: bool = True,
) -> SiglentConfiguration:
    driver_src = tmp_path / "driver-src"
    (driver_src / "siglent_spd3000").mkdir(parents=True)
    (driver_src / "siglent_spd3000" / "__init__.py").write_text(
        "# fixture\n", encoding="utf-8"
    )
    auth_file = tmp_path / "gateway-auth.toml"
    auth_file.write_text('token = "offline-test-token"\n', encoding="utf-8")
    return SiglentConfiguration(
        driver_src=driver_src,
        connection="gateway",
        identifier="offline.test:8765",
        gateway_auth_file=auth_file,
        acceptance_context="production_dispenser",
        topology="parallel_ch1",
        channel="CH1",
        expected_model="SPD3303X",
        expected_serial_number="SPD-BOUND",
        compliance_voltage_v=10.0,
        max_load_current_a=4.8,
        upward_step_a=0.2,
        control_enabled=control_enabled,
        timeout_s=5.0,
        min_command_interval_ms=100.0,
    )


def controller(
    tmp_path: Path,
    device: FakeDevice,
    *,
    config: SiglentConfiguration | None = None,
    durable_state_provider: UnloadedHilDurableStateProvider | None = None,
) -> tuple[DispenserPowerController, FakeSessionFactory]:
    factory = FakeSessionFactory(device)
    return (
        DispenserPowerController(
            config or configuration(tmp_path),
            session_factory=factory,
            durable_state_provider=durable_state_provider,
            clock=lambda: datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        ),
        factory,
    )


def unloaded_configuration(
    tmp_path: Path,
    *,
    control_enabled: bool = True,
) -> SiglentConfiguration:
    state_file = tmp_path / "unloaded-hil-trip.json"
    if not state_file.exists():
        state_file.write_text(
            json.dumps(
                {
                    "record_type": "initialized_state",
                    "schema_version": 1,
                    "initialized_at": "2026-09-03T12:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
    return replace(
        configuration(tmp_path, control_enabled=control_enabled),
        acceptance_context="unloaded_hil",
        unloaded_hil_state_file=state_file,
    )


def test_factory_uses_authenticated_gateway_and_global_write_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = configuration(tmp_path)
    calls: list[tuple[str, object]] = []
    sentinel = object()

    def load_gateway_auth(path: Path, *, required: bool) -> object:
        calls.append(("load_auth", (path, required)))
        return sentinel

    class FakeSPD3000:
        @staticmethod
        def connect(connection: str, identifier: str, **options: object) -> Any:
            calls.append(("connect", (connection, identifier, options)))
            return SimpleNamespace(ch1=object(), close=lambda: None)

    module = SimpleNamespace(
        load_gateway_auth=load_gateway_auth,
        SPD3000=FakeSPD3000,
    )

    def load_module(_path: Path) -> Any:
        return module

    monkeypatch.setattr(siglent, "_load_driver_module", load_module)

    session = SiglentDriverSessionFactory(config)()
    session.close()

    assert calls[0] == ("load_auth", (config.gateway_auth_file, True))
    assert calls[1] == (
        "connect",
        (
            "gateway",
            config.identifier,
            {
                "timeout_s": 5.0,
                "min_command_interval_ms": 100.0,
                "verify_writes_globally": True,
                "token": sentinel,
            },
        ),
    )


def test_development_source_validation_rejects_missing_public_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_src = tmp_path / "driver-src"
    package = driver_src / "siglent_spd3000"
    package.mkdir(parents=True)
    package_file = package / "__init__.py"
    package_file.write_text("# package\n", encoding="utf-8")
    fake_package = SimpleNamespace(
        __name__="siglent_spd3000",
        __file__=str(package_file),
        SPD3000=object(),
    )

    def load_package(_path: Path) -> Any:
        return fake_package

    monkeypatch.setattr(siglent, "_load_driver_module", load_package)

    with pytest.raises(ImportError, match="required public API"):
        siglent.validate_siglent_driver_installation(driver_src)


def test_development_source_validation_accepts_required_public_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_src = tmp_path / "driver-src"
    package = driver_src / "siglent_spd3000"
    package.mkdir(parents=True)
    package_file = package / "__init__.py"
    package_file.write_text("# package\n", encoding="utf-8")
    fake_package = SimpleNamespace(
        __name__="siglent_spd3000",
        __file__=str(package_file),
        SPD3000=object(),
        Channel=object(),
        ConnectionType=object(),
        OperatingMode=object(),
        OutputState=object(),
        load_gateway_auth=object(),
    )

    def load_package(_path: Path) -> Any:
        return fake_package

    monkeypatch.setattr(siglent, "_load_driver_module", load_package)

    siglent.validate_siglent_driver_installation(driver_src)


def test_driver_adapter_collects_state_snapshot_in_one_semantic_batch() -> None:
    events: list[str] = []

    class Batch:
        def __call__(self, function: Callable[[], Any] | None = None) -> Any:
            if function is None:
                return self

            def wrapped() -> Any:
                with self:
                    return function()

            return wrapped

        def __enter__(self) -> Batch:
            events.append("batch_enter")
            return self

        def __exit__(self, *_args: object) -> None:
            events.append("batch_exit")

    class Channel:
        @property
        def voltage(self) -> float:
            events.append("query_voltage")
            return 10.0

        @property
        def current(self) -> float:
            events.append("query_current")
            return 0.1

    class Measure:
        def voltage(self, channel: str) -> float:
            events.append(f"query_measured_voltage:{channel}")
            return 9.9

        def current(self, channel: str) -> float:
            events.append(f"query_measured_current:{channel}")
            return 0.08

        def power(self, channel: str) -> float:
            events.append(f"query_measured_power:{channel}")
            return 0.792

    class System:
        @property
        def status(self) -> Any:
            events.append("query_status")
            return SimpleNamespace(
                operating_mode=SimpleNamespace(value="parallel"),
                ch1=SimpleNamespace(
                    output=False,
                    regulation=SimpleNamespace(value="CC"),
                ),
            )

    device = SimpleNamespace(
        batch=Batch(),
        ch1=Channel(),
        measure=Measure(),
        system=System(),
        capabilities=SimpleNamespace(measure_power=True),
        close=lambda: None,
    )

    state = SiglentDriverSession(device, "CH1").read_channel_state()

    assert state.operating_mode == "parallel"
    assert state.current_setpoint_a == 0.1
    assert events == [
        "batch_enter",
        "query_status",
        "query_voltage",
        "query_current",
        "query_measured_voltage:CH1",
        "query_measured_current:CH1",
        "query_measured_power:CH1",
        "batch_exit",
    ]


def test_parallel_state_exposes_native_measurement_without_synthesizing_load(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.current_setpoint_a = 0.1
    device.measured_current_a = 0.08
    control, _factory = controller(tmp_path, device)

    state = control.read_state()

    assert state.configured_topology == "parallel_ch1"
    assert state.load_current_factor == 2
    assert state.commanded_load_current_limit_a == 0.2
    assert state.measured_native_channel_current_a == 0.08
    assert "measured_load_current_a" not in state.model_dump()
    assert state.expected_operating_mode == "parallel"
    assert state.driver_hardware_validation_status == (
        "validated_on_physical_instrument_via_gateway"
    )
    assert state.mcp_read_path_validation_status == (
        "validated_on_physical_instrument_via_authenticated_gateway"
    )
    assert state.mcp_actuation_validation_status == (
        "not_yet_validated_with_connected_dispenser"
    )
    assert state.safety_limits.deployment_native_current_ceiling_a == 2.4
    assert state.safety_limits.deployment_commanded_load_current_ceiling_a == 4.8
    assert state.safety_limits.acceptance_context == "production_dispenser"
    assert state.safety_limits.required_enable_confirmation == "confirmed_parallel_ch1"
    assert device.events == ["read_identity", "read_state", "close"]


def test_control_disabled_denies_before_opening_session(tmp_path: Path) -> None:
    device = FakeDevice()
    config = configuration(tmp_path, control_enabled=False)
    control, factory = controller(tmp_path, device, config=config)

    with pytest.raises(PowerControlError, match="disabled"):
        control.prepare()

    assert factory.calls == 0
    assert device.events == []


def test_workflow_absolute_ceiling_denies_before_opening_session(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    control, factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError, match="deterministic current ceiling"):
        control.set_current(target_current_a=5.0, expected_current_a=4.8)

    assert factory.calls == 0
    assert device.events == []


def test_identity_mismatch_makes_no_write(tmp_path: Path) -> None:
    device = FakeDevice()
    device.identity = replace(device.identity, serial_number="WRONG")
    control, _factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError, match="identity"):
        control.prepare()

    assert device.events == ["read_identity", "close"]


def test_prepare_uses_safe_order_and_verifies_zero_current(tmp_path: Path) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.voltage_setpoint_v = 2.0
    device.current_setpoint_a = 0.5
    control, _factory = controller(tmp_path, device)

    result = control.prepare()

    assert result.state.prepared_for_enable is True
    assert result.state.output_enabled is False
    assert result.state.native_current_setpoint_a == 0.0
    assert device.events == [
        "read_identity",
        "read_state",
        "set_output:False",
        "set_current:0.0",
        "set_voltage:10.0",
        "read_state",
        "close",
    ]


def test_prepare_rejects_topology_mismatch_before_write(tmp_path: Path) -> None:
    device = FakeDevice()
    device.operating_mode = "independent"
    control, _factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError, match="operating mode"):
        control.prepare()

    assert device.events == ["read_identity", "read_state", "close"]


def test_enable_requires_prepared_state_and_verifies_output(tmp_path: Path) -> None:
    device = FakeDevice()
    control, _factory = controller(tmp_path, device)

    result = control.enable(confirmation="confirmed_parallel_ch1")

    assert result.state.output_enabled is True
    assert "set_output:True" in device.events


@pytest.mark.parametrize(
    ("acceptance_context", "wrong_confirmation", "correct_confirmation"),
    [
        (
            "production_dispenser",
            "confirmed_no_dispenser_or_unapproved_load_connected",
            "confirmed_parallel_ch1",
        ),
        (
            "unloaded_hil",
            "confirmed_parallel_ch1",
            "confirmed_no_dispenser_or_unapproved_load_connected",
        ),
    ],
)
def test_acceptance_context_rejects_other_confirmation_before_session(
    tmp_path: Path,
    acceptance_context: PowerAcceptanceContext,
    wrong_confirmation: EnableConfirmation,
    correct_confirmation: EnableConfirmation,
) -> None:
    device = FakeDevice()
    config = replace(
        configuration(tmp_path),
        acceptance_context=acceptance_context,
        unloaded_hil_state_file=(
            tmp_path / "unloaded-hil-trip.json"
            if acceptance_context == "unloaded_hil"
            else None
        ),
    )
    if config.unloaded_hil_state_file is not None:
        config.unloaded_hil_state_file.write_text(
            json.dumps(
                {
                    "record_type": "initialized_state",
                    "schema_version": 1,
                    "initialized_at": "2026-09-03T12:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
    control, factory = controller(tmp_path, device, config=config)

    with pytest.raises(PowerControlError, match="startup-bound acceptance context"):
        control.enable(confirmation=wrong_confirmation)

    assert factory.calls == 0
    assert device.events == []

    result = control.enable(confirmation=correct_confirmation)
    assert result.state.output_enabled is True
    assert result.state.safety_limits.acceptance_context == acceptance_context
    assert (
        result.state.safety_limits.required_enable_confirmation == correct_confirmation
    )
    expected_validation_status = (
        "validated_on_unloaded_physical_instrument_via_authenticated_gateway"
        if acceptance_context == "unloaded_hil"
        else "not_yet_validated_with_connected_dispenser"
    )
    assert result.state.mcp_actuation_validation_status == expected_validation_status


def test_enable_rejects_nonzero_current_without_write(tmp_path: Path) -> None:
    device = FakeDevice()
    device.current_setpoint_a = 0.1
    control, _factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError, match="zero-current prepared"):
        control.enable(confirmation="confirmed_parallel_ch1")

    assert not any(event.startswith("set_") for event in device.events)


@pytest.mark.parametrize(
    ("current_drift_a", "voltage_drift_v"),
    [(0.1, None), (None, 9.0)],
)
def test_enable_drift_enters_verified_two_channel_recovery(
    tmp_path: Path,
    current_drift_a: float | None,
    voltage_drift_v: float | None,
) -> None:
    device = FakeDevice()
    device.enable_current_drift_a = current_drift_a
    device.enable_voltage_drift_v = voltage_drift_v
    control, _factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError) as captured:
        control.enable(confirmation="confirmed_parallel_ch1")

    assert captured.value.uncertain_output is False
    assert device.output_enabled is False
    assert device.ch2_output_enabled is False
    assert device.current_setpoint_a == 0.0
    assert device.ch2_current_setpoint_a == 0.0
    assert "set_channel_output:CH1:False" in device.events
    assert "set_channel_output:CH2:False" in device.events


def test_compare_and_set_translates_parallel_target_and_retry_is_write_free(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    control, _factory = controller(tmp_path, device)

    first = control.set_current(target_current_a=0.4, expected_current_a=0.2)
    event_count = len(device.events)
    retry = control.set_current(target_current_a=0.4, expected_current_a=0.2)

    assert first.wrote_hardware is True
    assert first.state.native_current_setpoint_a == 0.2
    assert first.state.commanded_load_current_limit_a == 0.4
    assert retry.wrote_hardware is False
    assert "set_current:0.2" in device.events[:event_count]
    assert not any(event.startswith("set_") for event in device.events[event_count:])


def test_compare_and_set_rejects_external_mismatch_and_large_step(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.15
    control, _factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError, match="does not match"):
        control.set_current(target_current_a=0.4, expected_current_a=0.2)
    with pytest.raises(PowerControlError, match="fixed operator-configured"):
        control.set_current(target_current_a=0.6, expected_current_a=0.3)

    assert not any(event.startswith("set_") for event in device.events)


def test_set_current_rejects_compliance_voltage_drift_before_write(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    device.voltage_setpoint_v = 9.0
    control, _factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError, match="compliance voltage"):
        control.set_current(target_current_a=0.4, expected_current_a=0.2)

    assert not any(event.startswith("set_") for event in device.events)


def test_set_current_verifies_compliance_voltage_after_write(tmp_path: Path) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    control, _factory = controller(tmp_path, device)
    original_set_current = FakeSession.set_current_a

    def set_and_drift_voltage(self: FakeSession, value: float) -> None:
        original_set_current(self, value)
        if value != 0:
            self.device.voltage_setpoint_v = 9.0

    FakeSession.set_current_a = set_and_drift_voltage
    try:
        with pytest.raises(PowerControlError, match="subsequently verified"):
            control.set_current(target_current_a=0.4, expected_current_a=0.2)
    finally:
        FakeSession.set_current_a = original_set_current

    assert device.output_enabled is False
    assert device.ch2_output_enabled is False
    assert device.current_setpoint_a == 0.0
    assert device.ch2_current_setpoint_a == 0.0


def test_safe_decrease_is_allowed(tmp_path: Path) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.2
    control, _factory = controller(tmp_path, device)

    result = control.set_current(target_current_a=0.2, expected_current_a=0.4)

    assert result.state.commanded_load_current_limit_a == 0.2
    assert "set_current:0.1" in device.events


def test_post_write_failure_forces_verified_off_and_zero(tmp_path: Path) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    control, _factory = controller(tmp_path, device)
    original_set_current = FakeSession.set_current_a

    def set_then_fail(self: FakeSession, value: float) -> None:
        original_set_current(self, value)
        if value != 0:
            self.device.fail_next_state = True

    FakeSession.set_current_a = set_then_fail
    try:
        with pytest.raises(PowerControlError) as captured:
            control.set_current(target_current_a=0.4, expected_current_a=0.2)
    finally:
        FakeSession.set_current_a = original_set_current

    assert captured.value.uncertain_output is False
    assert "subsequently verified" in captured.value.public_message
    assert "secret" not in captured.value.public_message
    assert device.output_enabled is False
    assert device.current_setpoint_a == 0.0


def test_unloaded_hil_post_write_failure_leaves_pending_state_across_restart(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    config = unloaded_configuration(tmp_path)
    control, _factory = controller(tmp_path, device, config=config)
    original_set_current = FakeSession.set_current_a

    def set_then_fail(self: FakeSession, value: float) -> None:
        original_set_current(self, value)
        if value != 0:
            self.device.fail_next_state = True

    FakeSession.set_current_a = set_then_fail
    try:
        with pytest.raises(PowerControlError, match="failed after a write"):
            control.set_current(target_current_a=0.4, expected_current_a=0.2)
    finally:
        FakeSession.set_current_a = original_set_current

    restarted, restarted_factory = controller(
        tmp_path,
        FakeDevice(),
        config=config,
    )
    with pytest.raises(PowerControlError, match="fail-closed"):
        restarted.prepare()
    assert restarted_factory.calls == 0
    diagnostic = restarted.read_state()
    assert diagnostic.unloaded_hil_interlock.failure_reason == (
        "unfinished_pending_operation"
    )


def test_uncertain_failure_reports_physical_verification_requirement(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.ch2_output_enabled = True
    device.current_setpoint_a = 0.1
    device.ch2_current_setpoint_a = 0.4
    device.fail_output_off_channels = {"CH2"}
    control, _factory = controller(tmp_path, device)
    original_set_current = FakeSession.set_current_a

    def set_then_fail(self: FakeSession, value: float) -> None:
        original_set_current(self, value)
        if value != 0:
            self.device.fail_next_state = True

    FakeSession.set_current_a = set_then_fail
    try:
        with pytest.raises(PowerControlError) as captured:
            control.set_current(target_current_a=0.4, expected_current_a=0.2)
    finally:
        FakeSession.set_current_a = original_set_current

    assert captured.value.uncertain_output is True
    assert "Output state may be unknown" in captured.value.public_message
    assert "physical verification" in captured.value.public_message
    assert "No retry" in captured.value.public_message
    assert device.output_enabled is False
    assert device.ch2_output_enabled is True


def test_shutdown_ignores_topology_mismatch_but_checks_identity_and_order(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.operating_mode = "independent"
    device.output_enabled = True
    device.ch2_output_enabled = True
    device.current_setpoint_a = 0.5
    device.ch2_current_setpoint_a = 0.7
    control, _factory = controller(tmp_path, device)

    result = control.shutdown()

    assert result.state.output_enabled is False
    assert result.state.native_current_setpoint_a == 0.0
    assert device.ch2_output_enabled is False
    assert device.ch2_current_setpoint_a == 0.0
    output_phase = [
        device.events.index("set_channel_output:CH1:False"),
        device.events.index("set_channel_output:CH2:False"),
        device.events.index("read_channel_output:CH1"),
        device.events.index("read_channel_output:CH2"),
    ]
    current_phase = [
        device.events.index("set_channel_current:CH1:0.0"),
        device.events.index("set_channel_current:CH2:0.0"),
        device.events.index("read_channel_current:CH1"),
        device.events.index("read_channel_current:CH2"),
    ]
    assert max(output_phase) < min(current_phase)


def test_shutdown_identity_mismatch_writes_neither_parallel_channel(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.identity = replace(device.identity, serial_number="WRONG")
    device.output_enabled = True
    device.ch2_output_enabled = True
    control, _factory = controller(tmp_path, device)

    with pytest.raises(PowerControlError, match="identity"):
        control.shutdown()

    assert device.events == ["read_identity", "close"]


@pytest.mark.parametrize("measured_current_a", [0.0011, -0.0011])
def test_unloaded_hil_outside_band_current_trips_and_uses_safe_order(
    tmp_path: Path,
    measured_current_a: float,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    device.measured_current_read_sequence = [measured_current_a, 0.001]
    config = unloaded_configuration(tmp_path)
    control, _factory = controller(tmp_path, device, config=config)

    with pytest.raises(PowerControlError, match="interlock tripped") as captured:
        control.set_current(target_current_a=0.4, expected_current_a=0.2)

    assert captured.value.uncertain_output is False
    trip_file = config.unloaded_hil_state_file
    assert trip_file is not None
    trip = UnloadedHilTripRecord.model_validate_json(
        trip_file.read_text(encoding="utf-8")
    )
    assert trip.observed_native_channel_current_a == measured_current_a
    assert trip.observed_at == datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    assert trip.operation == "set_dispenser_current"
    assert trip.schema_version == 2
    assert trip.reason == "post_operation_measured_native_current_outside_safe_band"

    first_measurement = device.events.index("read_measured_current")
    output_phase = [
        device.events.index("set_channel_output:CH1:False"),
        device.events.index("set_channel_output:CH2:False"),
        device.events.index("read_channel_output:CH1"),
        device.events.index("read_channel_output:CH2"),
    ]
    current_phase = [
        device.events.index("set_channel_current:CH1:0.0"),
        device.events.index("set_channel_current:CH2:0.0"),
        device.events.index("read_channel_current:CH1"),
        device.events.index("read_channel_current:CH2"),
    ]
    final_measurement = (
        len(device.events) - 1 - device.events[::-1].index("read_measured_current")
    )
    assert first_measurement < min(output_phase)
    assert max(output_phase) < min(current_phase)
    assert max(current_phase) < final_measurement
    assert device.output_enabled is False
    assert device.ch2_output_enabled is False
    assert device.current_setpoint_a == 0.0
    assert device.ch2_current_setpoint_a == 0.0


@pytest.mark.parametrize("measured_current_a", [-0.001, 0.0, 0.001])
def test_unloaded_hil_inclusive_safe_band_does_not_latch(
    tmp_path: Path,
    measured_current_a: float,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    device.measured_current_read_sequence = [measured_current_a]
    config = unloaded_configuration(tmp_path)
    control, _factory = controller(tmp_path, device, config=config)

    result = control.set_current(target_current_a=0.4, expected_current_a=0.2)

    assert result.state.commanded_load_current_limit_a == 0.4
    assert result.state.unloaded_hil_interlock.status == "unlatched"
    assert config.unloaded_hil_state_file is not None
    durable_state = FileUnloadedHilDurableStateProvider(
        config.unloaded_hil_state_file
    ).read_state()
    assert durable_state.trip is None
    assert durable_state.pending_operation is None


def test_post_trip_recovery_outside_safe_band_remains_uncertain(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.1
    device.measured_current_read_sequence = [0.0011, -0.0011]
    control, _factory = controller(
        tmp_path,
        device,
        config=unloaded_configuration(tmp_path),
    )

    with pytest.raises(PowerControlError, match="interlock tripped") as captured:
        control.set_current(target_current_a=0.4, expected_current_a=0.2)

    assert captured.value.uncertain_output is True


@pytest.mark.parametrize(
    "unavailable_value",
    [
        RuntimeError("measurement unavailable"),
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_unavailable_measurement_on_write_free_replay_durably_fails_closed(
    tmp_path: Path,
    unavailable_value: float | Exception,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.2
    device.measured_current_read_sequence = [unavailable_value, 0.0]
    config = unloaded_configuration(tmp_path)
    control, factory = controller(tmp_path, device, config=config)

    with pytest.raises(PowerControlError, match="durably latched") as captured:
        control.set_current(target_current_a=0.4, expected_current_a=0.2)

    assert captured.value.uncertain_output is False
    assert config.unloaded_hil_state_file is not None
    trip = UnloadedHilTripRecord.model_validate_json(
        config.unloaded_hil_state_file.read_text(encoding="utf-8")
    )
    assert trip.schema_version == 2
    assert trip.observed_native_channel_current_a is None
    assert trip.reason == "post_operation_measured_native_current_unavailable"
    first_measurement = device.events.index("read_measured_current")
    assert not any(
        event.startswith("set_") for event in device.events[:first_measurement]
    )
    assert device.events[first_measurement + 1 : first_measurement + 3] == [
        "set_channel_output:CH1:False",
        "set_channel_output:CH2:False",
    ]
    session_count = factory.calls
    with pytest.raises(PowerControlError, match="latched"):
        control.prepare()
    assert factory.calls == session_count


def test_unloaded_hil_trip_persists_across_restart_and_denies_before_session(
    tmp_path: Path,
) -> None:
    config = unloaded_configuration(tmp_path)
    tripping_device = FakeDevice()
    tripping_device.measured_current_read_sequence = [0.01, 0.0]
    first, _first_factory = controller(tmp_path, tripping_device, config=config)

    with pytest.raises(PowerControlError, match="interlock tripped"):
        first.prepare()

    restarted_device = FakeDevice()
    restarted, restarted_factory = controller(
        tmp_path,
        restarted_device,
        config=config,
    )
    with pytest.raises(PowerControlError, match="latched"):
        restarted.prepare()

    assert restarted_factory.calls == 0
    assert restarted_device.events == []

    diagnostic = restarted.read_state()
    assert diagnostic.unloaded_hil_interlock.applicable is True
    assert diagnostic.unloaded_hil_interlock.status == "latched"
    assert diagnostic.unloaded_hil_interlock.trip is not None
    assert diagnostic.unloaded_hil_interlock.trip.observed_native_channel_current_a == (
        0.01
    )
    assert not any(event.startswith("set_") for event in restarted_device.events)


def test_v041_trip_record_remains_latched_before_device_session(tmp_path: Path) -> None:
    config = unloaded_configuration(tmp_path)
    trip_file = config.unloaded_hil_state_file
    assert trip_file is not None
    trip_file.write_text(
        UnloadedHilTripRecord.legacy(
            observed_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
            observed_native_channel_current_a=0.001,
            operation="enable_dispenser_output",
        ).model_dump_json(),
        encoding="utf-8",
    )
    control, factory = controller(tmp_path, FakeDevice(), config=config)

    with pytest.raises(PowerControlError, match="latched"):
        control.prepare()

    assert factory.calls == 0


class FailingTripRecordStateProvider:
    def __init__(self) -> None:
        self.pending: UnloadedHilPendingOperationRecord | None = None

    def read_state(self) -> UnloadedHilDurableState:
        return UnloadedHilDurableState(
            trip=None,
            pending_operation=self.pending,
        )

    def begin_operation(
        self,
        *,
        operation: PowerMutationOperation,
        started_at: datetime,
    ) -> UnloadedHilPendingOperationRecord:
        assert self.pending is None
        self.pending = UnloadedHilPendingOperationRecord(
            record_type="pending_operation",
            schema_version=1,
            operation_id=UUID(int=1),
            started_at=started_at,
            operation=operation,
        )
        return self.pending

    def complete_operation(
        self,
        pending: UnloadedHilPendingOperationRecord,
        *,
        completed_at: datetime,
    ) -> None:
        del completed_at
        assert self.pending == pending
        self.pending = None

    def record_trip(self, record: UnloadedHilTripRecord) -> None:
        del record
        raise OSError("private persistence failure")


class ShutdownObservingTripStateProvider(FailingTripRecordStateProvider):
    def __init__(self, device: FakeDevice) -> None:
        super().__init__()
        self.device = device
        self.trip: UnloadedHilTripRecord | None = None

    def read_state(self) -> UnloadedHilDurableState:
        return UnloadedHilDurableState(
            trip=self.trip,
            pending_operation=None if self.trip is not None else self.pending,
        )

    def record_trip(self, record: UnloadedHilTripRecord) -> None:
        first_measurement = self.device.events.index("read_measured_current")
        shutdown_events = self.device.events[first_measurement + 1 :]
        assert "set_channel_output:CH1:False" in shutdown_events
        assert "set_channel_output:CH2:False" in shutdown_events
        assert "set_channel_current:CH1:0.0" in shutdown_events
        assert "set_channel_current:CH2:0.0" in shutdown_events
        assert shutdown_events[-1] == "read_measured_current"
        assert self.device.output_enabled is False
        assert self.device.ch2_output_enabled is False
        assert self.device.current_setpoint_a == 0.0
        assert self.device.ch2_current_setpoint_a == 0.0
        self.device.events.append("record_trip")
        self.trip = record
        self.pending = None


class UnreadableDurableStateProvider:
    def read_state(self) -> UnloadedHilDurableState:
        raise OSError("private read failure")

    def begin_operation(
        self,
        *,
        operation: PowerMutationOperation,
        started_at: datetime,
    ) -> UnloadedHilPendingOperationRecord:
        del operation, started_at
        raise AssertionError("begin_operation must not be called")

    def complete_operation(
        self,
        pending: UnloadedHilPendingOperationRecord,
        *,
        completed_at: datetime,
    ) -> None:
        del pending, completed_at
        raise AssertionError("complete_operation must not be called")

    def record_trip(self, record: UnloadedHilTripRecord) -> None:
        del record
        raise AssertionError("record_trip must not be called")


class FailingCompletionStateProvider(FailingTripRecordStateProvider):
    def complete_operation(
        self,
        pending: UnloadedHilPendingOperationRecord,
        *,
        completed_at: datetime,
    ) -> None:
        del completed_at
        assert self.pending == pending
        raise OSError("private completion persistence failure")


class FailingBeginStateProvider(FailingTripRecordStateProvider):
    def begin_operation(
        self,
        *,
        operation: PowerMutationOperation,
        started_at: datetime,
    ) -> UnloadedHilPendingOperationRecord:
        del operation, started_at
        raise OSError("private pending persistence failure")


def test_trip_persistence_occurs_only_after_verified_two_channel_shutdown(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.measured_current_read_sequence = [0.01, 0.0]
    state_provider = ShutdownObservingTripStateProvider(device)
    control, _factory = controller(
        tmp_path,
        device,
        config=unloaded_configuration(tmp_path),
        durable_state_provider=state_provider,
    )

    with pytest.raises(PowerControlError, match="interlock tripped"):
        control.prepare()

    assert device.events[-2:] == ["record_trip", "close"]


def test_unloaded_hil_persistence_failure_is_fail_closed_before_next_session(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.measured_current_read_sequence = [0.02, 0.0]
    state_provider = FailingTripRecordStateProvider()
    config = unloaded_configuration(tmp_path)
    control, factory = controller(
        tmp_path,
        device,
        config=config,
        durable_state_provider=state_provider,
    )

    with pytest.raises(PowerControlError, match="persistence") as captured:
        control.prepare()

    assert captured.value.uncertain_output is True
    session_count = factory.calls
    with pytest.raises(PowerControlError, match="fail-closed"):
        control.prepare()
    assert factory.calls == session_count

    diagnostic = control.read_state()
    assert diagnostic.unloaded_hil_interlock.status == "unavailable_fail_closed"
    assert diagnostic.unloaded_hil_interlock.failure_reason == (
        "persistence_unavailable"
    )

    restarted, restarted_factory = controller(
        tmp_path,
        FakeDevice(),
        config=config,
        durable_state_provider=state_provider,
    )
    with pytest.raises(PowerControlError, match="fail-closed"):
        restarted.prepare()
    assert restarted_factory.calls == 0
    restarted_diagnostic = restarted.read_state()
    assert restarted_diagnostic.unloaded_hil_interlock.failure_reason == (
        "unfinished_pending_operation"
    )


def test_unavailable_measurement_persistence_failure_still_shuts_down(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.2
    device.measured_current_read_sequence = [
        RuntimeError("measurement unavailable"),
        0.0,
    ]
    state_provider = FailingTripRecordStateProvider()
    control, factory = controller(
        tmp_path,
        device,
        config=unloaded_configuration(tmp_path),
        durable_state_provider=state_provider,
    )

    with pytest.raises(PowerControlError, match="persistence") as captured:
        control.set_current(target_current_a=0.4, expected_current_a=0.2)

    assert captured.value.uncertain_output is True
    assert device.output_enabled is False
    assert device.ch2_output_enabled is False
    assert device.current_setpoint_a == 0.0
    assert device.ch2_current_setpoint_a == 0.0
    session_count = factory.calls
    with pytest.raises(PowerControlError, match="fail-closed"):
        control.prepare()
    assert factory.calls == session_count


def test_pending_marker_failure_rejects_before_device_session(tmp_path: Path) -> None:
    control, factory = controller(
        tmp_path,
        FakeDevice(),
        config=unloaded_configuration(tmp_path),
        durable_state_provider=FailingBeginStateProvider(),
    )

    with pytest.raises(PowerControlError, match="Device access was rejected"):
        control.prepare()

    assert factory.calls == 0


def test_completion_marker_failure_shuts_down_and_restart_denies_session(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    state_provider = FailingCompletionStateProvider()
    config = unloaded_configuration(tmp_path)
    control, _factory = controller(
        tmp_path,
        device,
        config=config,
        durable_state_provider=state_provider,
    )

    with pytest.raises(PowerControlError, match="safe-completion marker"):
        control.prepare()

    assert device.output_enabled is False
    assert device.ch2_output_enabled is False
    assert device.current_setpoint_a == 0.0
    assert device.ch2_current_setpoint_a == 0.0
    restarted, restarted_factory = controller(
        tmp_path,
        FakeDevice(),
        config=config,
        durable_state_provider=state_provider,
    )
    with pytest.raises(PowerControlError, match="fail-closed"):
        restarted.prepare()
    assert restarted_factory.calls == 0


def test_unfinished_file_pending_operation_denies_after_restart_before_session(
    tmp_path: Path,
) -> None:
    config = unloaded_configuration(tmp_path)
    path = config.unloaded_hil_state_file
    assert path is not None
    FileUnloadedHilDurableStateProvider(path).begin_operation(
        operation="prepare_dispenser_power",
        started_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )
    restarted, restarted_factory = controller(
        tmp_path,
        FakeDevice(),
        config=config,
    )

    with pytest.raises(PowerControlError, match="fail-closed"):
        restarted.prepare()

    assert restarted_factory.calls == 0
    diagnostic = restarted.read_state()
    assert diagnostic.unloaded_hil_interlock.status == "unavailable_fail_closed"
    assert diagnostic.unloaded_hil_interlock.failure_reason == (
        "unfinished_pending_operation"
    )


def test_session_open_failure_leaves_pending_state_that_denies_after_restart(
    tmp_path: Path,
) -> None:
    config = unloaded_configuration(tmp_path)

    class FailingSessionFactory:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> FakeSession:
            self.calls += 1
            raise OSError("private source failure")

    failing_factory = FailingSessionFactory()
    first = DispenserPowerController(config, session_factory=failing_factory)
    with pytest.raises(PowerControlError, match="configured power-supply source"):
        first.prepare()
    assert failing_factory.calls == 1

    restarted, restarted_factory = controller(
        tmp_path,
        FakeDevice(),
        config=config,
    )
    with pytest.raises(PowerControlError, match="fail-closed"):
        restarted.prepare()
    assert restarted_factory.calls == 0
    diagnostic = restarted.read_state()
    assert diagnostic.unloaded_hil_interlock.failure_reason == (
        "unfinished_pending_operation"
    )


def test_unreadable_durable_state_denies_mutation_but_allows_diagnostic(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    control, factory = controller(
        tmp_path,
        device,
        config=unloaded_configuration(tmp_path),
        durable_state_provider=UnreadableDurableStateProvider(),
    )

    with pytest.raises(PowerControlError, match="unavailable"):
        control.prepare()
    assert factory.calls == 0

    diagnostic = control.read_state()
    assert diagnostic.unloaded_hil_interlock.status == "unavailable_fail_closed"
    assert factory.calls == 1
    assert device.events == ["read_identity", "read_state", "close"]


@pytest.mark.parametrize("operation", ["prepare", "enable", "set", "shutdown"])
def test_every_successful_unloaded_hil_mutation_queries_fresh_measured_current(
    tmp_path: Path,
    operation: str,
) -> None:
    case_path = tmp_path / operation
    case_path.mkdir()
    device = FakeDevice()
    config = unloaded_configuration(case_path)
    control, _factory = controller(case_path, device, config=config)

    if operation == "prepare":
        control.prepare()
    elif operation == "enable":
        control.enable(
            confirmation="confirmed_no_dispenser_or_unapproved_load_connected"
        )
    elif operation == "set":
        device.output_enabled = True
        control.set_current(target_current_a=0.2, expected_current_a=0.0)
    else:
        device.output_enabled = True
        device.current_setpoint_a = 0.1
        control.shutdown()

    assert device.events[-2:] == ["read_measured_current", "close"]


def test_unloaded_hil_read_diagnostic_is_write_free_and_does_not_create_latch(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    config = unloaded_configuration(tmp_path)
    assert config.unloaded_hil_state_file is not None
    config.unloaded_hil_state_file.unlink()
    control, _factory = controller(tmp_path, device, config=config)

    state = control.read_state()

    assert state.unloaded_hil_interlock.status == "unavailable_fail_closed"
    assert state.unloaded_hil_interlock.trip is None
    assert state.unloaded_hil_interlock.reset_authority == "out_of_band_human_only"
    assert not config.unloaded_hil_state_file.exists()
    assert device.events == ["read_identity", "read_state", "close"]


def test_missing_unloaded_hil_state_denies_mutation_before_session(
    tmp_path: Path,
) -> None:
    device = FakeDevice()
    config = unloaded_configuration(tmp_path)
    assert config.unloaded_hil_state_file is not None
    config.unloaded_hil_state_file.unlink()
    control, factory = controller(tmp_path, device, config=config)

    with pytest.raises(PowerControlError, match="fail-closed"):
        control.prepare()

    assert factory.calls == 0
    assert device.events == []


def test_unloaded_hil_state_provider_is_rejected_in_production_context(
    tmp_path: Path,
) -> None:
    config = configuration(tmp_path)
    with pytest.raises(ValueError, match="invalid outside unloaded_hil"):
        controller(
            tmp_path,
            FakeDevice(),
            config=config,
            durable_state_provider=FailingTripRecordStateProvider(),
        )

    state, _factory = controller(tmp_path, FakeDevice(), config=config)
    assert state.read_state().unloaded_hil_interlock.status == "not_applicable"
