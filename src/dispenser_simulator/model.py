"""Seeded virtual dispenser, power supply, and total-pressure gauge.

This module deliberately has no network, serial, VISA, OPC UA, gateway, or
hardware-driver imports.  All measurements are synthetic and all time is
virtual.  The seed and scenario are startup/harness inputs, never MCP tool
arguments.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from .metadata import NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A
from .observer import Observer

MBAR_TO_TORR = 760.0 / 1013.25
EPSILON = 1e-9


class SimulationError(RuntimeError):
    """A sanitized, model-visible simulator/tool error."""


@dataclass(frozen=True)
class ScenarioParameters:
    base_pressure_mbar: float = 1.5e-7
    thermal_tau_s: float = 120.0
    chamber_tau_s: float = 20.0
    impurity_pressure_gain_mbar_per_effective_unit: float = 0.0002
    rb_pressure_gain_mbar_per_effective_unit: float = 0.0005
    leak_fraction_per_s: float = 0.0
    # Legacy observation reference only; pressure never triggers power control.
    trip_pressure_mbar: float = 2.0e-5
    load_resistance_ohm: float = 0.15


SCENARIOS: dict[str, ScenarioParameters] = {
    "nominal_recovery": ScenarioParameters(base_pressure_mbar=1.45e-7),
    "slow_recovery": ScenarioParameters(base_pressure_mbar=1.7e-7, chamber_tau_s=60.0),
    # Legacy identifier: persistence now comes from finite release and removal.
    "persistent_total_pressure": ScenarioParameters(
        base_pressure_mbar=1.4e-7, chamber_tau_s=60.0
    ),
    "leak_rise": ScenarioParameters(
        base_pressure_mbar=2.0e-7, leak_fraction_per_s=0.0015
    ),
    # Legacy identifier retained for high-pressure observations, without a cutoff.
    "overpressure_guard": ScenarioParameters(
        base_pressure_mbar=2.2e-7,
        impurity_pressure_gain_mbar_per_effective_unit=0.002,
        trip_pressure_mbar=3.0e-6,
    ),
    **{
        name: ScenarioParameters()
        for name in (
            "gauge_dropout",
            "topology_drift",
            "enable_verification_fault",
            "identity_mismatch",
            "hil_current_trip",
            "hil_current_negative_trip",
            "hil_current_positive_boundary",
            "hil_current_negative_boundary",
            "hil_measurement_unavailable",
            "hil_measurement_nonfinite",
        )
    },
}


@dataclass(frozen=True)
class HiddenSimulatorConfig:
    """Startup-only simulator configuration.

    A launcher or test harness constructs this object.  No MCP tool exposes a
    setter for any field. No result returns ``seed`` or ``scenario``.
    """

    seed: str
    scenario: str
    acceptance_context: str = "production_dispenser"
    control_enabled: bool = True
    compliance_voltage_v: float = 10.0
    max_load_current_a: float = 4.8
    upward_step_a: float = 0.2
    observer_file: str | None = None

    def __post_init__(self) -> None:
        if not self.seed:
            raise ValueError("A non-empty hidden seed is required")
        if self.scenario not in SCENARIOS:
            raise ValueError("Unknown hidden scenario")
        if self.acceptance_context not in {"production_dispenser", "no_load_test"}:
            raise ValueError("Unsupported acceptance context")
        if not (0.0 < self.compliance_voltage_v <= 32.0):
            raise ValueError("Compliance voltage must be in (0, 32]")
        if not (0.0 < self.max_load_current_a <= 4.8):
            raise ValueError("Maximum load-current limit must be in (0, 4.8]")
        if not math.isclose(self.upward_step_a, 0.2, abs_tol=EPSILON):
            raise ValueError("parallel_ch1 upward step must be exactly 0.2 A")


@dataclass
class _State:
    virtual_time_s: float = 0.0
    live_mode: str = "parallel"
    ch1_output_on: bool = False
    ch2_output_on: bool = False
    native_ch1_current_setpoint_a: float = 0.0
    native_ch2_current_setpoint_a: float = 0.0
    native_ch1_voltage_setpoint_v: float = 0.0
    temperature: float = 0.0
    rb_remaining: float = 0.0
    impurity_remaining: float = 0.0
    rb_chamber: float = 0.0
    impurity_chamber: float = 0.0
    rb_removed: float = 0.0
    impurity_removed: float = 0.0
    pressure_state_mbar: float = 0.0
    prepared: bool = False
    pressure_reads: int = 0
    enable_attempts: int = 0
    hil_interlock_status: str = "unlatched"
    hil_interlock_trip: dict[str, Any] | None = None
    event_log: list[dict[str, Any]] = field(default_factory=list)


class SimulatedDispenser:
    """Dynamic simulator, deterministic for fixed inputs and an injected clock."""

    _epoch = datetime(2040, 1, 1, tzinfo=UTC)

    def __init__(
        self,
        config: HiddenSimulatorConfig,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._monotonic = monotonic
        self._last_interaction: float | None = None
        self.timing: dict[str, float] = {}
        self.params = SCENARIOS[config.scenario]
        self.state = _State(pressure_state_mbar=self.params.base_pressure_mbar)
        self.resistance_ohm = self.params.load_resistance_ohm * (
            0.9 + 0.2 * self._uniform("unit-resistance")
        )
        self.initial_rb = 0.7 + 0.6 * self._uniform("unit-rb-inventory")
        self.initial_impurity = 0.7 + 0.6 * self._uniform("unit-impurity-inventory")
        self.state.rb_remaining = self.initial_rb
        self.state.impurity_remaining = self.initial_impurity
        self.observer = Observer(config.observer_file)
        self._serial = (
            "SIMULATED-FOREIGN"
            if config.scenario == "identity_mismatch"
            else "SIMULATED-NOT-HARDWARE"
        )
        self._expected_serial = "SIMULATED-NOT-HARDWARE"
        self._dropout_offset = int(self._uniform("dropout-offset") * 5.0)
        self._record("startup", "synthetic simulator initialized")
        self.observe("init")

    @property
    def confirmation_field(self) -> str:
        if self.config.acceptance_context == "production_dispenser":
            return "parallel_connection_confirmation"
        return "no_load_test_connection_confirmation"

    @property
    def confirmation_literal(self) -> str:
        if self.config.acceptance_context == "production_dispenser":
            return "confirmed_parallel_ch1"
        return "confirmed_no_dispenser_or_unapproved_load_connected"

    def _uniform(self, label: str) -> float:
        digest = hashlib.sha256(f"{self.config.seed}|{label}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64)

    def _noise(self, label: str, amplitude: float) -> float:
        return (2.0 * self._uniform(label) - 1.0) * amplitude

    def _record(self, kind: str, detail: str) -> None:
        self.state.event_log.append(
            {
                "virtual_time_s": round(self.state.virtual_time_s, 6),
                "kind": kind,
                "detail": detail,
                "synthetic": True,
            }
        )

    def _timestamp(self) -> str:
        value = self._epoch + timedelta(seconds=self.state.virtual_time_s)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _close(left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=0.0, abs_tol=5e-7)

    def _identity_matches(self) -> bool:
        return self._serial == self._expected_serial

    def _assert_control_ready(
        self, *, require_topology: bool = True, allow_latched: bool = False
    ) -> None:
        if not self.config.control_enabled:
            raise SimulationError("Power control is disabled by startup policy.")
        if self.config.acceptance_context == "no_load_test" and not allow_latched:
            if self.state.hil_interlock_status == "latched":
                raise SimulationError(
                    "No-load test current stop is latched for this process; shutdown remains available."
                )
        if not self._identity_matches():
            raise SimulationError(
                "Power-supply identity does not match startup policy."
            )
        if require_topology and self.state.live_mode != "parallel":
            raise SimulationError(
                "Live power topology does not match parallel_ch1 policy."
            )

    def _force_energy_reducing_state(self) -> None:
        # Preserve the public recovery order: turn off and verify both outputs
        # before zeroing and verifying both current setpoints.
        self.state.ch1_output_on = False
        self.state.ch2_output_on = False
        if self.state.ch1_output_on or self.state.ch2_output_on:
            raise SimulationError("Synthetic two-output-off recovery was not verified.")
        self._record("recovery", "synthetic CH1 and CH2 off verified")
        self.state.native_ch1_current_setpoint_a = 0.0
        self.state.native_ch2_current_setpoint_a = 0.0
        if (
            self.state.native_ch1_current_setpoint_a != 0.0
            or self.state.native_ch2_current_setpoint_a != 0.0
        ):
            raise SimulationError(
                "Synthetic two-current-zero recovery was not verified."
            )
        self._record("recovery", "synthetic CH1 and CH2 current zero verified")
        self.state.prepared = False

    def _hil_measured_native_current_a(self) -> float:
        """Synthetic result of the separate no-load test current query."""

        if self.config.scenario == "hil_measurement_unavailable":
            raise SimulationError(
                "Synthetic no-load test measured-current query is unavailable."
            )
        if self.config.scenario == "hil_measurement_nonfinite":
            return math.nan
        if self.state.ch1_output_on and self.state.native_ch1_current_setpoint_a > 0.0:
            injected_observation = {
                "hil_current_positive_boundary": 0.001,
                "hil_current_negative_boundary": -0.001,
                "hil_current_trip": 0.002,
                "hil_current_negative_trip": -0.002,
            }.get(self.config.scenario)
            if injected_observation is not None:
                return injected_observation
        # A correctly unloaded output draws exact zero in this synthetic check,
        # regardless of a nonzero current-limit setpoint.
        return 0.0

    @staticmethod
    def _hil_measurement_is_in_safe_band(measured_current_a: float) -> bool:
        return math.isfinite(measured_current_a) and (
            abs(measured_current_a) <= NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A
        )

    def _complete_mutating_call(self, operation: str) -> None:
        """Apply the v0.4.3 no-load test post-mutation interlock check."""

        if self.config.acceptance_context != "no_load_test":
            return
        measured: float | None
        reason: str | None = None
        try:
            measured = self._hil_measured_native_current_a()
        except SimulationError:
            measured = None
            reason = "post_operation_measured_native_current_unavailable"
        else:
            if not math.isfinite(measured):
                measured = None
                reason = "post_operation_measured_native_current_unavailable"
            elif abs(measured) > NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A:
                reason = "post_operation_measured_native_current_outside_safe_band"
        self._record(
            "interlock",
            f"synthetic no-load test post-{operation} safe-band check",
        )
        if reason is None:
            return

        trip = {
            "observed_at": self._timestamp(),
            "observed_native_ch1_current_a": measured,
            "reason": reason,
            "mutating_operation": operation,
        }
        self.state.hil_interlock_status = "latched"
        self.state.hil_interlock_trip = self.state.hil_interlock_trip or trip

        # Shutdown happens in the same call before any result can be returned.
        # The fixed safe band is then checked again as a distinct measurement.
        self._force_energy_reducing_state()
        try:
            final_measured = self._hil_measured_native_current_a()
        except SimulationError:
            final_safe_band_verified = False
        else:
            final_safe_band_verified = self._hil_measurement_is_in_safe_band(
                final_measured
            )
        if final_safe_band_verified:
            detail = (
                "synthetic no-load test interlock latched; energy-reducing state "
                "and final safe band verified"
            )
        else:
            detail = (
                "synthetic no-load test interlock latched; energy-reducing state "
                "forced but final safe band not verified"
            )
        self._record("interlock", detail)
        raise SimulationError(
            "No-load test measured-current interlock latched and forced an energy-reducing state; further energizing is blocked for this process."
        )

    def _hil_interlock_result(self) -> dict[str, Any]:
        applicable = self.config.acceptance_context == "no_load_test"
        return {
            "applicable": applicable,
            "status": (
                self.state.hil_interlock_status if applicable else "not_applicable"
            ),
            "trip": self.state.hil_interlock_trip if applicable else None,
            "validation_status": "synthetic_model_not_hardware_validated",
        }

    REFERENCE_POWER_W = 4.8**2 * 0.15
    IMPURITY_REFERENCE_RATE_S = 1.0 / 2400.0
    RB_REFERENCE_RATE_S = 1.0 / 18000.0
    IMPURITY_ALPHA = 3.0
    RB_ALPHA = 6.0

    def _delivered(self) -> tuple[float, float, float]:
        if (
            not self.state.ch1_output_on
            or self.config.acceptance_context == "no_load_test"
        ):
            return 0.0, 0.0, 0.0
        current = min(
            2.0 * self.state.native_ch1_current_setpoint_a,
            self.state.native_ch1_voltage_setpoint_v / self.resistance_ohm,
        )
        voltage = current * self.resistance_ohm
        return current, voltage, current * voltage

    def _release_constants(self) -> tuple[float, float]:
        h = self.state.temperature
        return (
            self.RB_REFERENCE_RATE_S
            * math.expm1(self.RB_ALPHA * h)
            / math.expm1(self.RB_ALPHA),
            self.IMPURITY_REFERENCE_RATE_S
            * math.expm1(self.IMPURITY_ALPHA * h)
            / math.expm1(self.IMPURITY_ALPHA),
        )

    def _background_pressure(self) -> float:
        return self.params.base_pressure_mbar * (
            1.0 + self.params.leak_fraction_per_s * self.state.virtual_time_s
        )

    def _pressure_components(self) -> tuple[float, float, float]:
        return (
            self._background_pressure(),
            self.state.rb_chamber
            * self.params.rb_pressure_gain_mbar_per_effective_unit,
            self.state.impurity_chamber
            * self.params.impurity_pressure_gain_mbar_per_effective_unit,
        )

    @staticmethod
    def _transfer(
        remaining: float,
        chamber: float,
        removed: float,
        release_constant: float,
        removal_constant: float,
        dt: float,
    ) -> tuple[float, float, float]:
        # Exact coupled inventory -> chamber -> removed solution at frozen rates.
        released = remaining * -math.expm1(-release_constant * dt)
        difference = removal_constant - release_constant
        if abs(difference * dt) < 1e-8:
            injected_chamber = (
                remaining * release_constant * dt * math.exp(-removal_constant * dt)
            )
        else:
            injected_chamber = (
                remaining
                * release_constant
                * (math.exp(-release_constant * dt) - math.exp(-removal_constant * dt))
                / difference
            )
        next_chamber = chamber * math.exp(-removal_constant * dt) + injected_chamber
        next_remaining = remaining - released
        next_removed = removed + max(0.0, chamber + released - next_chamber)
        return next_remaining, next_chamber, next_removed

    def advance(self, seconds: float) -> None:
        """Advance causal material/thermal dynamics; not exposed as an MCP tool."""
        if not math.isfinite(seconds) or seconds <= 0.0 or seconds > 3600.0:
            raise ValueError("Virtual advance must be finite and in (0, 3600] seconds")
        remaining_time = seconds
        while remaining_time > 0.0:
            dt = min(remaining_time, 2.0)
            remaining_time -= dt
            self.state.virtual_time_s += dt
            if (
                self.config.scenario == "topology_drift"
                and self.state.virtual_time_s >= 120.0
                and self.state.live_mode == "parallel"
            ):
                self.state.live_mode = "independent"
                self._record("fault", "synthetic topology drift")
            _, _, power = self._delivered()
            target_h = power / self.REFERENCE_POWER_W
            self.state.temperature += -math.expm1(-dt / self.params.thermal_tau_s) * (
                target_h - self.state.temperature
            )
            rb_k, impurity_k = self._release_constants()
            removal_k = 1.0 / self.params.chamber_tau_s
            for species, k in (("rb", rb_k), ("impurity", impurity_k)):
                inventory, chamber, removed = self._transfer(
                    getattr(self.state, species + "_remaining"),
                    getattr(self.state, species + "_chamber"),
                    getattr(self.state, species + "_removed"),
                    k,
                    removal_k,
                    dt,
                )
                setattr(self.state, species + "_remaining", inventory)
                setattr(self.state, species + "_chamber", chamber)
                setattr(self.state, species + "_removed", removed)
            self.state.pressure_state_mbar = sum(self._pressure_components())
        self.observe("advance")

    def observe(
        self, kind: str, operation: str | None = None, status: str = "ok"
    ) -> None:
        if self.observer.path is None:
            return
        current, voltage, power = self._delivered()
        rb_k, impurity_k = self._release_constants()
        background, rb_pressure, impurity_pressure = self._pressure_components()
        self.observer.append(
            {
                "kind": kind,
                "operation": operation,
                "status": status,
                "model_revision": "two_inventory_v1",
                "profile_label": "synthetic_two_inventory",
                "virtual_time_s": self.state.virtual_time_s,
                "observed_at": self._timestamp(),
                "simulated": True,
                "parameters": {
                    "resistance_ohm": self.resistance_ohm,
                    "initial_rb_effective_units": self.initial_rb,
                    "initial_impurity_effective_units": self.initial_impurity,
                    "initial_rb_to_impurity_effective_ratio": self.initial_rb
                    / self.initial_impurity,
                    "thermal_tau_s": self.params.thermal_tau_s,
                    "chamber_tau_s": self.params.chamber_tau_s,
                    "reference_power_w": self.REFERENCE_POWER_W,
                    "impurity_reference_rate_s": self.IMPURITY_REFERENCE_RATE_S,
                    "impurity_alpha": self.IMPURITY_ALPHA,
                    "rb_reference_rate_s": self.RB_REFERENCE_RATE_S,
                    "rb_alpha": self.RB_ALPHA,
                    "rb_pressure_gain_mbar_per_effective_unit": (
                        self.params.rb_pressure_gain_mbar_per_effective_unit
                    ),
                    "impurity_pressure_gain_mbar_per_effective_unit": (
                        self.params.impurity_pressure_gain_mbar_per_effective_unit
                    ),
                },
                "state": {
                    "output_enabled": self.state.ch1_output_on,
                    "commanded_load_current_a": 2
                    * self.state.native_ch1_current_setpoint_a,
                    "delivered_current_a": current,
                    "delivered_voltage_v": voltage,
                    "heating_power_w": power,
                    "thermal_state": self.state.temperature,
                    "rb_remaining_fraction": self.state.rb_remaining / self.initial_rb,
                    "impurity_remaining_fraction": self.state.impurity_remaining
                    / self.initial_impurity,
                    "rb_remaining_effective_units": self.state.rb_remaining,
                    "impurity_remaining_effective_units": self.state.impurity_remaining,
                    "rb_emitted_effective_units": self.initial_rb
                    - self.state.rb_remaining,
                    "impurity_emitted_effective_units": self.initial_impurity
                    - self.state.impurity_remaining,
                    "rb_release_rate_effective_units_per_s": rb_k
                    * self.state.rb_remaining,
                    "impurity_release_rate_effective_units_per_s": impurity_k
                    * self.state.impurity_remaining,
                    "rb_chamber_effective_units": self.state.rb_chamber,
                    "impurity_chamber_effective_units": self.state.impurity_chamber,
                    "rb_removed_effective_units": self.state.rb_removed,
                    "impurity_removed_effective_units": self.state.impurity_removed,
                    "background_pressure_mbar": background,
                    "rb_pressure_mbar": rb_pressure,
                    "impurity_pressure_mbar": impurity_pressure,
                    "total_pressure_mbar": self.state.pressure_state_mbar,
                },
            }
        )

    @staticmethod
    def validate_elapsed(value: object) -> float:
        if type(value) not in (int, float):
            raise SimulationError(
                "elapsed_s must be a finite JSON number from 0 through 86400 seconds."
            )
        numeric = cast(int | float, value)
        if not 0 <= numeric <= 86400 or not math.isfinite(numeric):
            raise SimulationError(
                "elapsed_s must be a finite JSON number from 0 through 86400 seconds."
            )
        return float(numeric)

    def _call_tick(self, elapsed_s: float = 0.0) -> None:
        """Catch up under the preceding output state before observing or acting.

        Only physical interactions establish the monotonic anchor. Integration
        and recording duration therefore count toward the next interaction.
        """
        requested = self.validate_elapsed(elapsed_s)
        now = self._monotonic()
        wall = (
            max(0.0, now - self._last_interaction)
            if self._last_interaction is not None
            else 0.0
        )
        self._last_interaction = now
        advanced = max(requested, wall)
        remaining = advanced
        while remaining > 0:
            chunk = min(remaining, 3600.0)
            self.advance(chunk)
            remaining -= chunk
        self.timing = {
            "requested_elapsed_s": requested,
            "wall_elapsed_s": wall,
            "advanced_s": advanced,
            "virtual_time_s": self.state.virtual_time_s,
        }

    def _power_result(
        self,
        *,
        wrote_hardware: bool | None = None,
        simulator_state_mutated: bool | None = None,
    ) -> dict[str, Any]:
        delivered_current, load_voltage, _ = self._delivered()
        native_current = delivered_current / 2.0
        commanded_load_current = 2.0 * self.state.native_ch1_current_setpoint_a
        measurement_index = len(self.state.event_log)
        measured_current = max(
            0.0,
            native_current * (1.0 + self._noise(f"current-{measurement_index}", 0.002)),
        )
        measured_voltage = max(
            0.0,
            load_voltage * (1.0 + self._noise(f"voltage-{measurement_index}", 0.001)),
        )
        active_faults: list[str] = []
        if self.state.live_mode != "parallel":
            active_faults.append("synthetic_topology_mismatch")
        if not self._identity_matches():
            active_faults.append("synthetic_observed_serial_differs_from_expected")
        if self.state.hil_interlock_status == "latched":
            active_faults.append("synthetic_no_load_test_interlock_latched")
        elif self.state.hil_interlock_status == "unavailable_fail_closed":
            active_faults.append(
                "synthetic_no_load_test_interlock_unavailable_fail_closed"
            )
        result: dict[str, Any] = {
            "observed_at": self._timestamp(),
            "timing": dict(self.timing),
            "source": "synthetic.dispenser_conditioning.power_supply",
            "simulated": True,
            "synthetic_provenance": "seeded_dynamic_model_not_hardware_evidence",
            "configured_topology": "parallel_ch1",
            "topology_factor": 2,
            "native_channel": "CH1",
            "expected_operating_mode": "parallel",
            "live_operating_mode": self.state.live_mode,
            "topology_matches": self.state.live_mode == "parallel",
            "manufacturer": "SIGLENT (simulated identity only)",
            "model": "SPD3303X-SIMULATED",
            "serial_number": self._serial,
            "firmware_version": "SIMULATOR-1",
            "native_voltage_setpoint_v": self.state.native_ch1_voltage_setpoint_v,
            "native_current_setpoint_a": self.state.native_ch1_current_setpoint_a,
            "commanded_load_current_limit_a": commanded_load_current,
            "native_voltage_measurement_v": measured_voltage,
            "native_current_measurement_a": measured_current,
            "native_power_measurement_w": measured_voltage * measured_current,
            "output_enabled": self.state.ch1_output_on,
            "regulation_mode": (
                "CC"
                if self.state.ch1_output_on
                and native_current > 0.0
                and commanded_load_current * self.resistance_ohm
                <= self.state.native_ch1_voltage_setpoint_v
                else "CV"
            ),
            "compliance_voltage_matches": self._close(
                self.state.native_ch1_voltage_setpoint_v,
                self.config.compliance_voltage_v,
            ),
            "prepared": self.state.prepared,
            "safety_limits": {
                "acceptance_context": self.config.acceptance_context,
                "required_enable_confirmation_field": self.confirmation_field,
                "required_enable_confirmation_literal": self.confirmation_literal,
                "fixed_compliance_voltage_v": self.config.compliance_voltage_v,
                "operator_load_current_ceiling_a": self.config.max_load_current_a,
                "deployment_load_current_ceiling_a": 4.8,
                "native_current_ceiling_a": 2.4,
                "topology_hardware_load_current_ceiling_a": 6.4,
                "exact_upward_load_current_step_a": 0.2,
                "no_load_test_safe_measured_current_abs_a": (
                    NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A
                ),
            },
            "driver_hardware_validation_status": "synthetic_no_driver_or_hardware",
            "mcp_read_path_validation_status": "synthetic_only",
            "mcp_actuation_validation_status": "not_validated_on_physical_instrument",
            "safety_state": {
                # Inert legacy fields retained for existing result consumers.
                "simulator_guard_latched": False,
                "last_transition": "none",
                "guard_is_production_contract_feature": False,
            },
            "active_faults": active_faults,
            "no_load_test_interlock": self._hil_interlock_result(),
            "verifies_dispenser_activation": False,
        }
        if wrote_hardware is not None:
            # The field name is retained for client parity.  In this simulator it
            # means a synthetic state mutation, never a physical write.
            result["wrote_hardware"] = wrote_hardware
            result["write_was_synthetic"] = True
        if simulator_state_mutated is not None:
            result["simulator_state_mutated"] = simulator_state_mutated
        return result

    def read_dispenser_power_state(self, *, elapsed_s: float = 0.0) -> dict[str, Any]:
        self._call_tick(elapsed_s)
        self._record("read", "synthetic power state sampled")
        return self._power_result()

    def prepare_dispenser_power(self, *, elapsed_s: float = 0.0) -> dict[str, Any]:
        self._call_tick(elapsed_s)
        self._assert_control_ready()
        self.state.ch1_output_on = False
        self.state.ch2_output_on = False
        self.state.native_ch1_current_setpoint_a = 0.0
        self.state.native_ch2_current_setpoint_a = 0.0
        self.state.native_ch1_voltage_setpoint_v = self.config.compliance_voltage_v
        self.state.prepared = True
        self._record("write", "synthetic prepare sequence completed")
        self._complete_mutating_call("prepare_dispenser_power")
        return self._power_result(wrote_hardware=False, simulator_state_mutated=True)

    def enable_dispenser_output(
        self, confirmation: str, *, elapsed_s: float = 0.0
    ) -> dict[str, Any]:
        self._call_tick(elapsed_s)
        self._assert_control_ready()
        if confirmation != self.confirmation_literal:
            raise SimulationError(
                "The startup-context confirmation literal is invalid."
            )
        if self.state.ch1_output_on:
            raise SimulationError("Dispenser output is already enabled.")
        if not self.state.prepared:
            raise SimulationError("Power must be prepared before output enable.")
        if not self._close(self.state.native_ch1_current_setpoint_a, 0.0):
            raise SimulationError("Native current is not zero.")
        if not self._close(
            self.state.native_ch1_voltage_setpoint_v,
            self.config.compliance_voltage_v,
        ):
            raise SimulationError("Compliance voltage does not match startup policy.")
        self.state.enable_attempts += 1
        self.state.ch1_output_on = True
        if (
            self.config.scenario == "enable_verification_fault"
            and self.state.enable_attempts == 1
        ):
            self.state.ch1_output_on = False
            self.state.ch2_output_on = False
            self.state.native_ch1_current_setpoint_a = 0.0
            self.state.native_ch2_current_setpoint_a = 0.0
            self.state.prepared = False
            self._record(
                "recovery", "synthetic enable verification fault recovered off"
            )
            self._complete_mutating_call("enable_dispenser_output")
            raise SimulationError(
                "Synthetic post-write verification failed; recovery reported both outputs off and both current setpoints zero."
            )
        self._record("write", "synthetic output enabled at zero current")
        self._complete_mutating_call("enable_dispenser_output")
        return self._power_result(wrote_hardware=False, simulator_state_mutated=True)

    def set_dispenser_current(
        self,
        target_current_a: float,
        expected_current_a: float,
        *,
        elapsed_s: float = 0.0,
    ) -> dict[str, Any]:
        self._call_tick(elapsed_s)
        self._assert_control_ready()
        for label, value in (
            ("target_current_a", target_current_a),
            ("expected_current_a", expected_current_a),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SimulationError(f"{label} must be a finite number.")
            if not math.isfinite(float(value)):
                raise SimulationError(f"{label} must be a finite number.")
            if not (0.0 <= float(value) <= 6.4):
                raise SimulationError(f"{label} is outside the topology range.")
        target = float(target_current_a)
        expected = float(expected_current_a)
        if target > min(self.config.max_load_current_a, 4.8) + EPSILON:
            raise SimulationError("Target exceeds the fixed load-current ceiling.")
        native_target = target / 2.0
        native_expected = expected / 2.0
        if not self._close(native_target * 10.0, round(native_target * 10.0)):
            raise SimulationError(
                "Target is not representable at native 0.1 A resolution."
            )
        permitted_relation = target < expected - EPSILON or self._close(
            target - expected, self.config.upward_step_a
        )
        if not permitted_relation:
            raise SimulationError(
                "Transition must be an exact 0.2 A increase or a decrease."
            )
        if not self.state.ch1_output_on:
            raise SimulationError("Dispenser output is not enabled.")
        if not self._close(
            self.state.native_ch1_voltage_setpoint_v,
            self.config.compliance_voltage_v,
        ):
            raise SimulationError("Compliance voltage does not match startup policy.")

        live_native = self.state.native_ch1_current_setpoint_a
        if self._close(live_native, native_target):
            self._record("retry", "synthetic compare-and-set replay was write-free")
            self._complete_mutating_call("set_dispenser_current")
            return self._power_result(
                wrote_hardware=False, simulator_state_mutated=False
            )
        if not self._close(live_native, native_expected):
            raise SimulationError(
                "Live commanded current does not match expected_current_a."
            )

        self.state.native_ch1_current_setpoint_a = native_target
        self._record("write", "synthetic current compare-and-set completed")
        self._complete_mutating_call("set_dispenser_current")
        return self._power_result(wrote_hardware=False, simulator_state_mutated=True)

    def shutdown_dispenser_power(self) -> dict[str, Any]:
        self._call_tick()
        self._assert_control_ready(require_topology=False, allow_latched=True)
        self.state.ch1_output_on = False
        self.state.ch2_output_on = False
        self.state.native_ch1_current_setpoint_a = 0.0
        self.state.native_ch2_current_setpoint_a = 0.0
        self.state.prepared = False
        self._record("write", "synthetic two-channel shutdown completed")
        self._complete_mutating_call("shutdown_dispenser_power")
        return self._power_result(wrote_hardware=False, simulator_state_mutated=True)

    def read_vacuum_pressure(self, *, elapsed_s: float = 0.0) -> dict[str, Any]:
        self._call_tick(elapsed_s)
        self.state.pressure_reads += 1
        if (
            self.config.scenario == "gauge_dropout"
            and (self.state.pressure_reads + self._dropout_offset) % 5 == 0
        ):
            self._record("fault", "synthetic total-pressure observation unavailable")
            raise SimulationError(
                "Synthetic total-pressure observation is temporarily unavailable."
            )
        pressure = self.state.pressure_state_mbar * (
            1.0
            + self._noise(
                f"pressure-{self.state.pressure_reads}",
                0.012,
            )
        )
        pressure = max(pressure, 1e-12)
        self._record("read", "synthetic G1 total-pressure sample")
        return {
            "observed_at": self._timestamp(),
            "timing": dict(self.timing),
            "pressure_mbar": pressure,
            "pressure_torr": pressure * MBAR_TO_TORR,
            "source": "synthetic.pfeiffer_hicube_neo.pvviewer.g1_total_pressure",
            "p1_drive_serial_number": "SIMULATED-P1-NOT-HARDWARE",
            "is_total_gauge_pressure": True,
            "is_rubidium_partial_pressure": False,
            "verifies_dispenser_activation": False,
            "simulated": True,
            "synthetic_provenance": "seeded_dynamic_model_not_hardware_evidence",
        }


class ToolRouter:
    """Strict closed-schema dispatcher shared by tests and the MCP adapter."""

    TOOL_NAMES = (
        "read_dispenser_power_state",
        "prepare_dispenser_power",
        "enable_dispenser_output",
        "set_dispenser_current",
        "shutdown_dispenser_power",
        "read_vacuum_pressure",
    )

    def __init__(self, simulator: SimulatedDispenser):
        self.simulator = simulator

    @staticmethod
    def _object(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        if arguments is None:
            return {}
        if not isinstance(arguments, Mapping):
            raise SimulationError("Tool arguments must be a JSON object.")
        return dict(arguments)

    @staticmethod
    def _exact_keys(arguments: Mapping[str, Any], required: set[str]) -> None:
        actual = set(arguments)
        if actual != required:
            raise SimulationError(
                "Tool arguments do not match the closed input schema."
            )

    def call(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        status = "error"
        try:
            result = self._call(name, arguments)
            status = "ok"
            return result
        finally:
            # Unknown caller text must not enter the human trace as instructions.
            operation = name if name in self.TOOL_NAMES else "unknown_tool"
            self.simulator.observe("call", operation, status)

    def _call(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        args = self._object(arguments)
        elapsed = 0.0
        if name in self.TOOL_NAMES and name != "shutdown_dispenser_power":
            elapsed = self.simulator.validate_elapsed(args.pop("elapsed_s", 0.0))
        if name == "read_dispenser_power_state":
            self._exact_keys(args, set())
            return self.simulator.read_dispenser_power_state(elapsed_s=elapsed)
        if name == "prepare_dispenser_power":
            self._exact_keys(args, set())
            return self.simulator.prepare_dispenser_power(elapsed_s=elapsed)
        if name == "enable_dispenser_output":
            field = self.simulator.confirmation_field
            self._exact_keys(args, {field})
            value = args[field]
            if not isinstance(value, str):
                raise SimulationError("Enable confirmation must be a string.")
            return self.simulator.enable_dispenser_output(value, elapsed_s=elapsed)
        if name == "set_dispenser_current":
            self._exact_keys(args, {"target_current_a", "expected_current_a"})
            return self.simulator.set_dispenser_current(
                args["target_current_a"], args["expected_current_a"], elapsed_s=elapsed
            )
        if name == "shutdown_dispenser_power":
            self._exact_keys(args, set())
            return self.simulator.shutdown_dispenser_power()
        if name == "read_vacuum_pressure":
            self._exact_keys(args, set())
            return self.simulator.read_vacuum_pressure(elapsed_s=elapsed)
        raise SimulationError("Unknown tool name.")
