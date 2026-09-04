"""Siglent SPD3000 integration and deterministic dispenser safety controller."""

from __future__ import annotations

import importlib
import math
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, NoReturn

from dispenser_conditioning_mcp.config import (
    PARALLEL_LOAD_CURRENT_CEILING_A,
    PARALLEL_NATIVE_CURRENT_CEILING_A,
    SiglentConfiguration,
)
from dispenser_conditioning_mcp.interlock import FileUnloadedHilDurableStateProvider
from dispenser_conditioning_mcp.power_domain import (
    DRIVER_VALIDATION_STATUS,
    MCP_READ_PATH_VALIDATION_STATUS,
    POWER_SOURCE_LABEL,
    UNLOADED_HIL_SAFE_MEASURED_CURRENT_ABS_A,
    WORKFLOW_ABSOLUTE_CURRENT_CEILING_A,
    DeviceIdentity,
    DispenserPowerState,
    EnableConfirmation,
    NativeChannel,
    PowerAcceptanceContext,
    PowerActionResult,
    PowerControlError,
    PowerMutationOperation,
    PowerSafetyLimits,
    PowerSupplySession,
    PowerSupplySessionFactory,
    RawChannelState,
    UnloadedHilDurableStateProvider,
    UnloadedHilInterlockState,
    UnloadedHilPendingOperationRecord,
    UnloadedHilTripRecord,
    mcp_actuation_validation_status,
    required_enable_confirmation,
)

_IMPORT_LOCK = threading.Lock()


class _HandledUnloadedHilSafetyFailure(PowerControlError):
    """Mark a durable-safety failure whose shutdown has already been attempted."""


class SiglentDriverSession:
    """Adapt the hardware-validated driver's public API to one bound channel."""

    def __init__(self, device: Any, channel: str) -> None:
        self._device = device
        self._channel_name = channel
        self._channel = getattr(device, channel.lower())

    def read_identity(self) -> DeviceIdentity:
        identity = self._device.idn
        return DeviceIdentity(
            manufacturer=str(identity.manufacturer),
            model=str(identity.model.value),
            serial_number=str(identity.serial_number),
            firmware_version=str(identity.firmware_version),
        )

    def read_channel_state(self) -> RawChannelState:
        @self._device.batch
        def read_snapshot() -> tuple[Any, Any, Any, Any, Any, Any]:
            return (
                self._device.system.status,
                self._channel.voltage,
                self._channel.current,
                self._device.measure.voltage(self._channel_name),
                self._device.measure.current(self._channel_name),
                (
                    self._device.measure.power(self._channel_name)
                    if bool(self._device.capabilities.measure_power)
                    else None
                ),
            )

        (
            status,
            voltage_setpoint_v,
            current_setpoint_a,
            measured_voltage_v,
            measured_current_a,
            measured_power_w,
        ) = read_snapshot()
        channel_status = getattr(status, self._channel_name.lower())
        return RawChannelState(
            operating_mode=str(status.operating_mode.value),
            voltage_setpoint_v=float(voltage_setpoint_v),
            current_setpoint_a=float(current_setpoint_a),
            measured_voltage_v=float(measured_voltage_v),
            measured_current_a=float(measured_current_a),
            measured_power_w=(
                None if measured_power_w is None else float(measured_power_w)
            ),
            output_enabled=bool(channel_status.output),
            regulation_mode=str(channel_status.regulation.value),
        )

    def read_output_enabled(self) -> bool:
        return bool(self._channel.output)

    def read_current_setpoint_a(self) -> float:
        return float(self._channel.current)

    def read_measured_current_a(self) -> float:
        return float(self._device.measure.current(self._channel_name))

    def read_channel_output_enabled(self, channel: NativeChannel) -> bool:
        return bool(self._channel_object(channel).output)

    def read_channel_current_setpoint_a(self, channel: NativeChannel) -> float:
        return float(self._channel_object(channel).current)

    def set_voltage_v(self, value: float) -> None:
        self._channel.voltage = value

    def set_current_a(self, value: float) -> None:
        self._channel.current = value

    def set_channel_current_a(self, channel: NativeChannel, value: float) -> None:
        self._channel_object(channel).current = value

    def set_output_enabled(self, enabled: bool) -> None:
        self._channel.output = enabled

    def set_channel_output_enabled(self, channel: NativeChannel, enabled: bool) -> None:
        self._channel_object(channel).output = enabled

    @contextmanager
    def atomic_write_batch(self) -> Generator[None]:
        with self._device.batch():
            yield

    def close(self) -> None:
        self._device.close()

    def _channel_object(self, channel: NativeChannel) -> Any:
        return getattr(self._device, channel.lower())


class SiglentDriverSessionFactory:
    """Create one driver session from hidden operator connection settings."""

    def __init__(self, configuration: SiglentConfiguration) -> None:
        self._configuration = configuration

    def __call__(self) -> PowerSupplySession:
        module = _load_driver_module(self._configuration.driver_src)
        options: dict[str, object] = {
            "timeout_s": self._configuration.timeout_s,
            "min_command_interval_ms": self._configuration.min_command_interval_ms,
            "verify_writes_globally": True,
        }
        token = module.load_gateway_auth(
            self._configuration.gateway_auth_file,
            required=True,
        )
        if token is None:
            raise RuntimeError("Gateway authentication did not provide a token.")
        options["token"] = token
        device = module.SPD3000.connect(
            self._configuration.connection,
            self._configuration.identifier,
            **options,
        )
        return SiglentDriverSession(device, self._configuration.channel)


class DispenserPowerController:
    """Apply topology-aware, deterministic safety checks around one supply."""

    def __init__(
        self,
        configuration: SiglentConfiguration,
        *,
        session_factory: PowerSupplySessionFactory | None = None,
        durable_state_provider: UnloadedHilDurableStateProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._configuration = configuration
        self._session_factory = session_factory or SiglentDriverSessionFactory(
            configuration
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._interlock_failure_reason: Literal["persistence_unavailable"] | None = None
        self._active_pending_operation: UnloadedHilPendingOperationRecord | None = None
        if configuration.acceptance_context == "unloaded_hil":
            if durable_state_provider is not None:
                self._durable_state_provider = durable_state_provider
            elif configuration.unloaded_hil_state_file is not None:
                self._durable_state_provider = FileUnloadedHilDurableStateProvider(
                    configuration.unloaded_hil_state_file
                )
            else:
                raise ValueError(
                    "unloaded_hil requires a durable operation/trip state provider"
                )
        else:
            if durable_state_provider is not None:
                raise ValueError(
                    "a durable unloaded-HIL state provider is invalid outside "
                    "unloaded_hil"
                )
            self._durable_state_provider = None

    @property
    def acceptance_context(self) -> PowerAcceptanceContext:
        """Return the immutable startup-bound physical acceptance context."""

        return self._configuration.acceptance_context

    def read_state(self) -> DispenserPowerState:
        """Read one state snapshot without changing the supply."""

        with self._lock:
            session = self._open_session()
            try:
                identity = session.read_identity()
                self._require_identity(identity)
                raw = session.read_channel_state()
                return self._state(identity, raw)
            except PowerControlError:
                raise
            except Exception as error:
                raise PowerControlError(
                    "Power-supply state is unavailable from the configured source."
                ) from error
            finally:
                _close_quietly(session)

    def prepare(self) -> PowerActionResult:
        """Force output off, set zero current, and set fixed compliance voltage."""

        def operation(
            session: PowerSupplySession, identity: DeviceIdentity
        ) -> PowerActionResult:
            initial = session.read_channel_state()
            self._require_topology(initial)
            with session.atomic_write_batch():
                session.set_output_enabled(False)
                session.set_current_a(0.0)
                session.set_voltage_v(self._configuration.compliance_voltage_v)
            state = self._state(identity, session.read_channel_state())
            if not state.prepared_for_enable:
                raise RuntimeError("prepared-state verification failed")
            return PowerActionResult(
                action="prepare_dispenser_power",
                wrote_hardware=True,
                state=state,
            )

        return self._control("prepare_dispenser_power", operation)

    def enable(self, *, confirmation: EnableConfirmation) -> PowerActionResult:
        """Enable output only from the verified, zero-current prepared state."""

        required_confirmation = required_enable_confirmation(
            self._configuration.acceptance_context
        )
        if confirmation != required_confirmation:
            raise PowerControlError(
                "Enable was rejected because its physical confirmation does not "
                "match the startup-bound acceptance context."
            )

        def operation(
            session: PowerSupplySession, identity: DeviceIdentity
        ) -> PowerActionResult:
            initial = self._state(identity, session.read_channel_state())
            self._require_topology_from_state(initial)
            if initial.output_enabled:
                raise PowerControlError(
                    "Enable was rejected because the configured output is already on."
                )
            if not initial.prepared_for_enable:
                raise PowerControlError(
                    "Enable was rejected because the supply is not in the verified "
                    "zero-current prepared state."
                )
            with session.atomic_write_batch():
                session.set_output_enabled(True)
            state = self._state(identity, session.read_channel_state())
            if (
                not state.topology_matches
                or not state.output_enabled
                or not self._native_close(state.native_current_setpoint_a, 0.0)
                or not state.compliance_voltage_matches
            ):
                raise RuntimeError("output-on verification failed")
            return PowerActionResult(
                action="enable_dispenser_output",
                wrote_hardware=True,
                state=state,
            )

        return self._control("enable_dispenser_output", operation)

    def set_current(
        self, *, target_current_a: float, expected_current_a: float
    ) -> PowerActionResult:
        """Compare-and-set one absolute load-current limit with bounded upward motion."""

        target_native = self._validated_native_target(
            target_current_a, enforce_ceiling=True
        )
        expected_native = self._validated_native_target(
            expected_current_a, enforce_ceiling=False
        )

        def operation(
            session: PowerSupplySession, identity: DeviceIdentity
        ) -> PowerActionResult:
            initial = self._state(identity, session.read_channel_state())
            self._require_topology_from_state(initial)
            if not initial.output_enabled:
                raise PowerControlError(
                    "Current change was rejected because the configured output is off."
                )
            if not initial.compliance_voltage_matches:
                raise PowerControlError(
                    "Current change was rejected because the live voltage setpoint "
                    "does not match the operator-fixed compliance voltage."
                )

            live_native = initial.native_current_setpoint_a
            live_is_target = self._native_close(live_native, target_native)
            live_is_expected = self._native_close(live_native, expected_native)

            if live_is_target:
                self._require_valid_transition(expected_current_a, target_current_a)
                return PowerActionResult(
                    action="set_dispenser_current",
                    wrote_hardware=False,
                    state=initial,
                )
            if not live_is_expected:
                raise PowerControlError(
                    "Current change was rejected because the live setpoint does not "
                    "match expected_current_a. Re-read state before deciding what to do."
                )
            self._require_valid_transition(expected_current_a, target_current_a)

            with session.atomic_write_batch():
                session.set_current_a(target_native)
            state = self._state(identity, session.read_channel_state())
            if (
                not state.topology_matches
                or not state.output_enabled
                or not state.compliance_voltage_matches
                or not self._native_close(
                    state.native_current_setpoint_a, target_native
                )
            ):
                raise RuntimeError("current-setpoint verification failed")
            return PowerActionResult(
                action="set_dispenser_current",
                wrote_hardware=True,
                state=state,
            )

        return self._control("set_dispenser_current", operation)

    def shutdown(self) -> PowerActionResult:
        """Turn output off before zeroing current; topology mismatch does not block it."""

        def operation(
            session: PowerSupplySession, identity: DeviceIdentity
        ) -> PowerActionResult:
            channels = self._shutdown_channels
            with session.atomic_write_batch():
                for channel in channels:
                    session.set_channel_output_enabled(channel, False)
            for channel in channels:
                if session.read_channel_output_enabled(channel):
                    raise RuntimeError("output-off verification failed")
            with session.atomic_write_batch():
                for channel in channels:
                    session.set_channel_current_a(channel, 0.0)
            for channel in channels:
                if not self._native_close(
                    session.read_channel_current_setpoint_a(channel), 0.0
                ):
                    raise RuntimeError("zero-current verification failed")
            state = self._state(identity, session.read_channel_state())
            if state.output_enabled or not self._native_close(
                state.native_current_setpoint_a, 0.0
            ):
                raise RuntimeError("shutdown verification failed")
            return PowerActionResult(
                action="shutdown_dispenser_power",
                wrote_hardware=True,
                state=state,
            )

        return self._control("shutdown_dispenser_power", operation)

    def _control(
        self,
        operation_name: PowerMutationOperation,
        operation: Callable[[PowerSupplySession, DeviceIdentity], PowerActionResult],
    ) -> PowerActionResult:
        with self._lock:
            if not self._configuration.control_enabled:
                raise PowerControlError(
                    "Power control is disabled by operator startup policy."
                )
            self._require_interlock_allows_mutation()
            pending = self._begin_unloaded_hil_operation(operation_name)
            self._active_pending_operation = pending
            try:
                session = self._open_session()
            except Exception:
                self._active_pending_operation = None
                raise
            write_started = False
            try:
                identity = session.read_identity()
                self._require_identity(identity)

                class WriteTrackingSession:
                    """Mark the first write while preserving the session protocol."""

                    def __getattr__(self, name: str) -> Any:
                        return getattr(session, name)

                    def set_voltage_v(self, value: float) -> None:
                        nonlocal write_started
                        write_started = True
                        session.set_voltage_v(value)

                    def set_current_a(self, value: float) -> None:
                        nonlocal write_started
                        write_started = True
                        session.set_current_a(value)

                    def set_channel_current_a(
                        self, channel: NativeChannel, value: float
                    ) -> None:
                        nonlocal write_started
                        write_started = True
                        session.set_channel_current_a(channel, value)

                    def set_output_enabled(self, enabled: bool) -> None:
                        nonlocal write_started
                        write_started = True
                        session.set_output_enabled(enabled)

                    def set_channel_output_enabled(
                        self, channel: NativeChannel, enabled: bool
                    ) -> None:
                        nonlocal write_started
                        write_started = True
                        session.set_channel_output_enabled(channel, enabled)

                tracked = WriteTrackingSession()
                result = operation(tracked, identity)  # type: ignore[arg-type]
                if (
                    self._configuration.acceptance_context == "unloaded_hil"
                    and result.state.unloaded_hil_interlock.status != "unlatched"
                ):
                    raise PowerControlError(
                        "Unloaded-HIL interlock state became unavailable during the "
                        "power action. Control is fail-closed."
                    )
                self._enforce_unloaded_hil_safe_current_band(
                    session,
                    operation=result.action,
                )
                self._complete_unloaded_hil_operation(pending, session)
                return result
            except _HandledUnloadedHilSafetyFailure:
                raise
            except PowerControlError as error:
                if write_started:
                    safe = self._best_effort_shutdown(session)
                    raise PowerControlError(
                        (
                            "The power action failed after a write; output off and "
                            "zero current were subsequently verified. No retry was "
                            "attempted."
                            if safe
                            else "The power action failed after a write. Output state "
                            "may be unknown; physical verification or hardware "
                            "shutdown is required. No retry was attempted."
                        ),
                        uncertain_output=not safe,
                    ) from error
                raise
            except Exception as error:
                if write_started:
                    safe = self._best_effort_shutdown(session)
                    if safe:
                        message = (
                            "The power action failed after a write; output off and "
                            "zero current were subsequently verified. No retry was "
                            "attempted."
                        )
                    else:
                        message = (
                            "The power action failed after a write. Output state may be "
                            "unknown; physical verification or hardware shutdown is "
                            "required. No retry was attempted."
                        )
                    raise PowerControlError(
                        message, uncertain_output=not safe
                    ) from error
                raise PowerControlError(
                    "The power action failed before any commanded write. Re-read the "
                    "supply state and inspect local diagnostics."
                ) from error
            finally:
                _close_quietly(session)
                self._active_pending_operation = None

    def _open_session(self) -> PowerSupplySession:
        try:
            return self._session_factory()
        except Exception as error:
            raise PowerControlError(
                "The configured power-supply source is unavailable."
            ) from error

    def _require_identity(self, identity: DeviceIdentity) -> None:
        if (
            identity.model != self._configuration.expected_model
            or identity.serial_number != self._configuration.expected_serial_number
        ):
            raise PowerControlError(
                "Power-supply identity does not match the operator-bound model and "
                "serial number; no write was issued."
            )

    def _require_topology(self, raw: RawChannelState) -> None:
        if raw.operating_mode != self._expected_operating_mode:
            raise PowerControlError(
                "Power action was rejected because live operating mode does not "
                "match the operator-configured topology."
            )

    @staticmethod
    def _require_topology_from_state(state: DispenserPowerState) -> None:
        if not state.topology_matches:
            raise PowerControlError(
                "Power action was rejected because live operating mode does not "
                "match the operator-configured topology."
            )

    def _require_valid_transition(self, expected: float, target: float) -> None:
        if target > expected and not math.isclose(target, expected):
            if not math.isclose(
                target - expected,
                self._configuration.upward_step_a,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise PowerControlError(
                    "Current increase must equal the fixed operator-configured "
                    "upward step."
                )

    def _validated_native_target(self, value: float, *, enforce_ceiling: bool) -> float:
        rendered = float(value)
        hardware_ceiling = 3.2 * self._configuration.load_current_factor
        if not math.isfinite(rendered) or rendered < 0 or rendered > hardware_ceiling:
            raise PowerControlError(
                "Current value is outside the configured topology hardware range."
            )
        if enforce_ceiling:
            effective_ceiling = min(
                self._configuration.max_load_current_a,
                WORKFLOW_ABSOLUTE_CURRENT_CEILING_A,
                hardware_ceiling,
            )
            if rendered > effective_ceiling:
                raise PowerControlError(
                    "Target current exceeds a deterministic current ceiling."
                )
        native = rendered / self._configuration.load_current_factor
        if Decimal(str(native)) % Decimal(str(self._native_current_resolution)) != 0:
            raise PowerControlError(
                "Current value does not align to the native driver resolution after "
                "topology translation."
            )
        return native

    def _best_effort_shutdown(self, session: PowerSupplySession) -> bool:
        channels = self._shutdown_channels
        output_commands_ok = True
        try:
            with session.atomic_write_batch():
                for channel in channels:
                    session.set_channel_output_enabled(channel, False)
        except Exception:
            output_commands_ok = False
        outputs_verified_off = output_commands_ok
        for channel in channels:
            try:
                if session.read_channel_output_enabled(channel):
                    outputs_verified_off = False
            except Exception:
                outputs_verified_off = False

        current_commands_ok = True
        try:
            with session.atomic_write_batch():
                for channel in channels:
                    session.set_channel_current_a(channel, 0.0)
        except Exception:
            current_commands_ok = False
        currents_verified_zero = current_commands_ok
        for channel in channels:
            try:
                if not self._native_close(
                    session.read_channel_current_setpoint_a(channel), 0.0
                ):
                    currents_verified_zero = False
            except Exception:
                currents_verified_zero = False
        return outputs_verified_off and currents_verified_zero

    def _require_interlock_allows_mutation(self) -> None:
        state = self._unloaded_hil_interlock_state()
        if state.status == "latched":
            raise PowerControlError(
                "The unloaded-HIL interlock is latched. Mutating power requests are "
                "blocked until an out-of-band human emergency reset is completed."
            )
        if state.status == "unavailable_fail_closed":
            raise PowerControlError(
                "Unloaded-HIL interlock state is unavailable. Mutating power "
                "requests are fail-closed; inspect local operator diagnostics."
            )

    def _begin_unloaded_hil_operation(
        self,
        operation: PowerMutationOperation,
    ) -> UnloadedHilPendingOperationRecord | None:
        if self._configuration.acceptance_context != "unloaded_hil":
            return None
        try:
            assert self._durable_state_provider is not None
            return self._durable_state_provider.begin_operation(
                operation=operation,
                started_at=self._clock().astimezone(UTC),
            )
        except Exception as error:
            self._interlock_failure_reason = "persistence_unavailable"
            raise PowerControlError(
                "The unloaded-HIL pending operation could not be established "
                "durably. Device access was rejected and control is fail-closed."
            ) from error

    def _complete_unloaded_hil_operation(
        self,
        pending: UnloadedHilPendingOperationRecord | None,
        session: PowerSupplySession,
    ) -> None:
        if pending is None:
            return
        try:
            assert self._durable_state_provider is not None
            self._durable_state_provider.complete_operation(
                pending,
                completed_at=self._clock().astimezone(UTC),
            )
        except Exception as error:
            self._interlock_failure_reason = "persistence_unavailable"
            shutdown_verified = self._best_effort_shutdown(session)
            raise _HandledUnloadedHilSafetyFailure(
                (
                    "The unloaded-HIL safe-completion marker could not be committed. "
                    "Both outputs off and both current setpoints zero were verified, "
                    "but control remains durably fail-closed for operator review."
                    if shutdown_verified
                    else "The unloaded-HIL safe-completion marker could not be "
                    "committed. Output state may be unknown; physical verification "
                    "or hardware shutdown is required. Control remains fail-closed."
                ),
                uncertain_output=True,
            ) from error

    def _enforce_unloaded_hil_safe_current_band(
        self,
        session: PowerSupplySession,
        *,
        operation: Literal[
            "prepare_dispenser_power",
            "enable_dispenser_output",
            "set_dispenser_current",
            "shutdown_dispenser_power",
        ],
    ) -> None:
        if self._configuration.acceptance_context != "unloaded_hil":
            return
        try:
            measured_current_a = session.read_measured_current_a()
        except Exception:
            self._raise_measurement_unavailable(session, operation=operation)
        if not math.isfinite(measured_current_a):
            self._raise_measurement_unavailable(session, operation=operation)
        if abs(measured_current_a) <= UNLOADED_HIL_SAFE_MEASURED_CURRENT_ABS_A:
            return

        record = UnloadedHilTripRecord.outside_safe_band(
            observed_at=self._clock().astimezone(UTC),
            observed_native_channel_current_a=measured_current_a,
            operation=operation,
        )
        shutdown_verified = self._best_effort_shutdown(session)
        if shutdown_verified:
            try:
                recovery_current_a = session.read_measured_current_a()
                shutdown_verified = (
                    math.isfinite(recovery_current_a)
                    and abs(recovery_current_a)
                    <= UNLOADED_HIL_SAFE_MEASURED_CURRENT_ABS_A
                )
            except Exception:
                shutdown_verified = False

        persistence_verified = False
        try:
            assert self._durable_state_provider is not None
            self._durable_state_provider.record_trip(record)
            persistence_verified = (
                self._durable_state_provider.read_state().trip == record
            )
        except Exception:
            self._interlock_failure_reason = "persistence_unavailable"
            persistence_verified = False

        if persistence_verified and shutdown_verified:
            message = (
                "The unloaded-HIL interlock tripped after measured current exceeded "
                "the fixed safe band. Both outputs off, both current setpoints zero, "
                "and measured current within the fixed safe band were verified. "
                "Further control is latched until "
                "an out-of-band human emergency reset is completed."
            )
        elif persistence_verified:
            message = (
                "The unloaded-HIL interlock tripped after measured current exceeded "
                "the fixed safe band. Output state may be unknown; physical verification or "
                "hardware shutdown is required. Further control is latched until "
                "an out-of-band human emergency reset is completed."
            )
        else:
            message = (
                "Measured current outside the fixed safe band triggered the "
                "unloaded-HIL interlock, "
                "but durable trip persistence could not be verified. Control is "
                "fail-closed; physical verification or hardware shutdown and local "
                "operator review are required."
            )
        raise _HandledUnloadedHilSafetyFailure(
            message,
            uncertain_output=not (persistence_verified and shutdown_verified),
        )

    def _raise_measurement_unavailable(
        self,
        session: PowerSupplySession,
        *,
        operation: Literal[
            "prepare_dispenser_power",
            "enable_dispenser_output",
            "set_dispenser_current",
            "shutdown_dispenser_power",
        ],
    ) -> NoReturn:
        record = UnloadedHilTripRecord.measurement_unavailable(
            observed_at=self._clock().astimezone(UTC),
            operation=operation,
        )
        shutdown_verified = self._best_effort_shutdown(session)
        if shutdown_verified:
            try:
                recovery_current_a = session.read_measured_current_a()
                shutdown_verified = (
                    math.isfinite(recovery_current_a)
                    and abs(recovery_current_a)
                    <= UNLOADED_HIL_SAFE_MEASURED_CURRENT_ABS_A
                )
            except Exception:
                shutdown_verified = False

        persistence_verified = False
        try:
            assert self._durable_state_provider is not None
            self._durable_state_provider.record_trip(record)
            persistence_verified = (
                self._durable_state_provider.read_state().trip == record
            )
        except Exception:
            self._interlock_failure_reason = "persistence_unavailable"
        raise _HandledUnloadedHilSafetyFailure(
            (
                "The unloaded-HIL measured-current check was unavailable or "
                "non-finite and was durably latched. Both outputs off, both current "
                "setpoints zero, and recovery current within the fixed safe band "
                "were verified."
                if persistence_verified and shutdown_verified
                else "The unloaded-HIL measured-current check was unavailable or "
                "non-finite. Recovery was verified, but durable latch persistence "
                "was not; control remains fail-closed for this process."
                if shutdown_verified
                else "The unloaded-HIL measured-current check was unavailable or "
                "non-finite. Control is fail-closed; output state may be unknown, "
                "so physical verification or hardware shutdown is required."
            ),
            uncertain_output=not (persistence_verified and shutdown_verified),
        )

    def _unloaded_hil_interlock_state(self) -> UnloadedHilInterlockState:
        if self._configuration.acceptance_context != "unloaded_hil":
            return UnloadedHilInterlockState(
                applicable=False,
                status="not_applicable",
                trip=None,
                validation_status="not_applicable",
            )
        if self._interlock_failure_reason is not None:
            return UnloadedHilInterlockState(
                applicable=True,
                status="unavailable_fail_closed",
                trip=None,
                failure_reason=self._interlock_failure_reason,
                validation_status=(
                    "offline_simulation_only_not_retested_on_physical_instrument"
                ),
            )
        try:
            assert self._durable_state_provider is not None
            durable_state = self._durable_state_provider.read_state()
        except Exception:
            self._interlock_failure_reason = "persistence_unavailable"
            return UnloadedHilInterlockState(
                applicable=True,
                status="unavailable_fail_closed",
                trip=None,
                failure_reason="persistence_unavailable",
                validation_status=(
                    "offline_simulation_only_not_retested_on_physical_instrument"
                ),
            )
        if durable_state.trip is not None:
            return UnloadedHilInterlockState(
                applicable=True,
                status="latched",
                trip=durable_state.trip,
                validation_status=(
                    "offline_simulation_only_not_retested_on_physical_instrument"
                ),
            )
        pending = durable_state.pending_operation
        if pending is not None and pending != self._active_pending_operation:
            return UnloadedHilInterlockState(
                applicable=True,
                status="unavailable_fail_closed",
                trip=None,
                failure_reason="unfinished_pending_operation",
                validation_status=(
                    "offline_simulation_only_not_retested_on_physical_instrument"
                ),
            )
        return UnloadedHilInterlockState(
            applicable=True,
            status="unlatched",
            trip=None,
            validation_status=(
                "offline_simulation_only_not_retested_on_physical_instrument"
            ),
        )

    def _state(
        self, identity: DeviceIdentity, raw: RawChannelState
    ) -> DispenserPowerState:
        load_factor = self._configuration.load_current_factor
        topology_matches = raw.operating_mode == self._expected_operating_mode
        compliance_voltage_matches = math.isclose(
            raw.voltage_setpoint_v,
            self._configuration.compliance_voltage_v,
            rel_tol=0.0,
            abs_tol=self._native_voltage_resolution / 2,
        )
        prepared = (
            topology_matches
            and not raw.output_enabled
            and self._native_close(raw.current_setpoint_a, 0.0)
            and compliance_voltage_matches
        )
        return DispenserPowerState(
            observed_at=self._clock().astimezone(UTC),
            source=POWER_SOURCE_LABEL,
            configured_topology=self._configuration.topology,
            load_current_factor=load_factor,
            expected_operating_mode=self._expected_operating_mode,
            live_operating_mode=raw.operating_mode,
            topology_matches=topology_matches,
            selected_native_channel=self._configuration.channel,
            manufacturer=identity.manufacturer,
            model=identity.model,
            serial_number=identity.serial_number,
            firmware_version=identity.firmware_version,
            native_voltage_setpoint_v=raw.voltage_setpoint_v,
            native_current_setpoint_a=raw.current_setpoint_a,
            commanded_load_current_limit_a=(raw.current_setpoint_a * load_factor),
            measured_native_channel_voltage_v=raw.measured_voltage_v,
            measured_native_channel_current_a=raw.measured_current_a,
            measured_native_channel_power_w=raw.measured_power_w,
            output_enabled=raw.output_enabled,
            regulation_mode=raw.regulation_mode,
            compliance_voltage_matches=compliance_voltage_matches,
            prepared_for_enable=prepared,
            unloaded_hil_interlock=self._unloaded_hil_interlock_state(),
            safety_limits=PowerSafetyLimits(
                control_enabled=self._configuration.control_enabled,
                acceptance_context=self._configuration.acceptance_context,
                required_enable_confirmation=required_enable_confirmation(
                    self._configuration.acceptance_context
                ),
                fixed_compliance_voltage_v=(self._configuration.compliance_voltage_v),
                operator_max_load_current_a=(self._configuration.max_load_current_a),
                deployment_native_current_ceiling_a=(PARALLEL_NATIVE_CURRENT_CEILING_A),
                deployment_commanded_load_current_ceiling_a=(
                    PARALLEL_LOAD_CURRENT_CEILING_A
                ),
                workflow_absolute_current_ceiling_a=(
                    WORKFLOW_ABSOLUTE_CURRENT_CEILING_A
                ),
                topology_hardware_current_ceiling_a=3.2 * load_factor,
                upward_step_a=self._configuration.upward_step_a,
                native_voltage_resolution_v=self._native_voltage_resolution,
                native_current_resolution_a=self._native_current_resolution,
                unloaded_hil_safe_measured_current_abs_a=(
                    UNLOADED_HIL_SAFE_MEASURED_CURRENT_ABS_A
                ),
            ),
            driver_hardware_validation_status=DRIVER_VALIDATION_STATUS,
            mcp_read_path_validation_status=MCP_READ_PATH_VALIDATION_STATUS,
            mcp_actuation_validation_status=mcp_actuation_validation_status(
                self._configuration.acceptance_context
            ),
        )

    @property
    def _expected_operating_mode(self) -> Literal["parallel"]:
        return "parallel"

    @property
    def _native_voltage_resolution(self) -> float:
        return 0.001 if self._configuration.expected_model == "SPD3303X" else 0.01

    @property
    def _native_current_resolution(self) -> float:
        return 0.001 if self._configuration.expected_model == "SPD3303X" else 0.01

    def _native_close(self, left: float, right: float) -> bool:
        return math.isclose(
            left,
            right,
            rel_tol=0.0,
            abs_tol=self._native_current_resolution / 2,
        )

    @property
    def _shutdown_channels(self) -> tuple[NativeChannel, ...]:
        return ("CH1", "CH2")


def _load_driver_module(driver_src: Path) -> Any:
    """Import the exact operator-selected source tree without installing it."""

    expected_root = driver_src.resolve()
    with _IMPORT_LOCK:
        existing = sys.modules.get("siglent_spd3000")
        if existing is None:
            sys.path.insert(0, str(expected_root))
            try:
                importlib.invalidate_caches()
                existing = importlib.import_module("siglent_spd3000")
            finally:
                try:
                    sys.path.remove(str(expected_root))
                except ValueError:
                    pass
        _require_module_origin(existing, expected_root)
        return existing


def validate_siglent_driver_installation(driver_src: Path) -> None:
    """Import the operator-selected development source and require its public API."""

    module = _load_driver_module(driver_src)
    required_api = (
        "SPD3000",
        "Channel",
        "ConnectionType",
        "OperatingMode",
        "OutputState",
        "load_gateway_auth",
    )
    if any(not hasattr(module, name) for name in required_api):
        raise ImportError(
            "The configured Siglent source lacks the required public API."
        )


def _require_module_origin(module: ModuleType, expected_root: Path) -> None:
    raw_file = getattr(module, "__file__", None)
    if not isinstance(raw_file, str):
        raise ImportError("The configured Siglent package has no source origin.")
    origin = Path(raw_file).resolve()
    if not origin.is_relative_to(expected_root):
        raise ImportError("A different Siglent driver package is already loaded.")


def _close_quietly(session: PowerSupplySession) -> None:
    try:
        session.close()
    except Exception:
        pass
