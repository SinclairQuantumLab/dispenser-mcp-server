"""Domain contracts for deterministic dispenser power control."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

WORKFLOW_ABSOLUTE_CURRENT_CEILING_A = 4.8
NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A = 0.001
DRIVER_VALIDATION_STATUS = "validated_on_physical_instrument_via_gateway"
MCP_READ_PATH_VALIDATION_STATUS = (
    "validated_on_physical_instrument_via_authenticated_gateway"
)
MCP_PRODUCTION_ACTUATION_VALIDATION_STATUS = (
    "not_yet_validated_with_connected_dispenser"
)
MCP_NO_LOAD_TEST_ACTUATION_VALIDATION_STATUS = (
    "validated_on_unloaded_physical_instrument_via_authenticated_gateway"
)
PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION = "confirmed_parallel_ch1"
NO_LOAD_TEST_CONFIRMATION = "confirmed_no_dispenser_or_unapproved_load_connected"
POWER_SOURCE_LABEL = "siglent_spd3000.semantic_driver"
NativeChannel = Literal["CH1", "CH2"]
PowerAcceptanceContext = Literal["production_dispenser", "no_load_test"]
PowerMutationOperation = Literal[
    "prepare_dispenser_power",
    "enable_dispenser_output",
    "set_dispenser_current",
    "shutdown_dispenser_power",
]
EnableConfirmation = Literal[
    "confirmed_parallel_ch1",
    "confirmed_no_dispenser_or_unapproved_load_connected",
]
BelowNoLoadTestSafeBandCurrent = Annotated[
    float,
    Field(
        strict=True,
        allow_inf_nan=False,
        lt=-NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A,
    ),
]
AboveNoLoadTestSafeBandCurrent = Annotated[
    float,
    Field(
        strict=True,
        allow_inf_nan=False,
        gt=NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A,
    ),
]
SignedOutsideNoLoadTestSafeBandCurrent = (
    BelowNoLoadTestSafeBandCurrent | AboveNoLoadTestSafeBandCurrent
)


class PowerControlError(RuntimeError):
    """A sanitized deterministic-policy or device-control failure."""

    def __init__(self, public_message: str, *, uncertain_output: bool = False) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.uncertain_output = uncertain_output


@dataclass(frozen=True)
class DeviceIdentity:
    """Fresh public identity returned by the semantic driver."""

    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str


@dataclass(frozen=True)
class RawChannelState:
    """Fresh native channel state, without topology-derived measurements."""

    operating_mode: str
    voltage_setpoint_v: float
    current_setpoint_a: float
    measured_voltage_v: float
    measured_current_a: float
    measured_power_w: float | None
    output_enabled: bool
    regulation_mode: str


class PowerSupplySession(Protocol):
    """Minimal semantic-driver surface used by the safety controller."""

    def read_identity(self) -> DeviceIdentity:
        """Return a fresh identity query."""

        raise NotImplementedError

    def read_channel_state(self) -> RawChannelState:
        """Return fresh configured-channel setpoints, measurements, and status."""

        raise NotImplementedError

    def read_output_enabled(self) -> bool:
        """Return fresh configured-channel output state."""

        raise NotImplementedError

    def read_current_setpoint_a(self) -> float:
        """Return the fresh native current setpoint."""

        raise NotImplementedError

    def read_measured_current_a(self) -> float:
        """Return one fresh native-channel measured-current query."""

        raise NotImplementedError

    def read_channel_output_enabled(self, channel: NativeChannel) -> bool:
        """Return fresh output state for an explicit internal safety channel."""

        raise NotImplementedError

    def read_channel_current_setpoint_a(self, channel: NativeChannel) -> float:
        """Return fresh current setpoint for an explicit safety channel."""

        raise NotImplementedError

    def set_voltage_v(self, value: float) -> None:
        """Set the native channel voltage."""

        raise NotImplementedError

    def set_current_a(self, value: float) -> None:
        """Set the native channel current limit."""

        raise NotImplementedError

    def set_channel_current_a(self, channel: NativeChannel, value: float) -> None:
        """Set one explicit internal safety channel current limit."""

        raise NotImplementedError

    def set_output_enabled(self, enabled: bool) -> None:
        """Set configured-channel output state."""

        raise NotImplementedError

    def set_channel_output_enabled(self, channel: NativeChannel, enabled: bool) -> None:
        """Set one explicit internal safety channel output state."""

        raise NotImplementedError

    def atomic_write_batch(self) -> AbstractContextManager[None]:
        """Collect verified semantic writes into one non-interleaved batch."""

        raise NotImplementedError

    def close(self) -> None:
        """Close the one-call driver session."""

        raise NotImplementedError


class PowerSupplySessionFactory(Protocol):
    """Create one semantic-driver session for a bounded tool call."""

    def __call__(self) -> PowerSupplySession:
        """Return a new session or raise an integration exception."""

        raise NotImplementedError


class OutsideBandNoLoadTestTripRecord(BaseModel):
    """Immutable current-format trip record for one signed outside-band measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime = Field(description="UTC timestamp of trip detection.")
    observed_native_channel_current_a: SignedOutsideNoLoadTestSafeBandCurrent
    reason: Literal["post_operation_measured_native_current_outside_safe_band"]
    operation: PowerMutationOperation

    @model_validator(mode="after")
    def require_outside_band_observation(self) -> Self:
        if (
            abs(self.observed_native_channel_current_a)
            <= NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A
        ):
            raise ValueError("trip current must be outside the fixed safe band")
        return self


class UnavailableNoLoadTestTripRecord(BaseModel):
    """Immutable current-format trip record for an unavailable measured-current query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime = Field(description="UTC timestamp of trip detection.")
    observed_native_channel_current_a: None
    reason: Literal["post_operation_measured_native_current_unavailable"]
    operation: PowerMutationOperation


NoLoadTestTripPayload = (
    OutsideBandNoLoadTestTripRecord | UnavailableNoLoadTestTripRecord
)


class NoLoadTestTripRecord(RootModel[NoLoadTestTripPayload]):
    """Structurally strict union of current no-load test trip variants."""

    model_config = ConfigDict(frozen=True)

    @classmethod
    def outside_safe_band(
        cls,
        *,
        observed_at: datetime,
        observed_native_channel_current_a: float,
        operation: PowerMutationOperation,
    ) -> Self:
        return cls(
            root=OutsideBandNoLoadTestTripRecord(
                observed_at=observed_at,
                observed_native_channel_current_a=observed_native_channel_current_a,
                reason="post_operation_measured_native_current_outside_safe_band",
                operation=operation,
            )
        )

    @classmethod
    def measurement_unavailable(
        cls,
        *,
        observed_at: datetime,
        operation: PowerMutationOperation,
    ) -> Self:
        return cls(
            root=UnavailableNoLoadTestTripRecord(
                observed_at=observed_at,
                observed_native_channel_current_a=None,
                reason="post_operation_measured_native_current_unavailable",
                operation=operation,
            )
        )

    @property
    def observed_at(self) -> datetime:
        return self.root.observed_at

    @property
    def observed_native_channel_current_a(self) -> float | None:
        return self.root.observed_native_channel_current_a

    @property
    def reason(self) -> str:
        return self.root.reason

    @property
    def operation(self) -> PowerMutationOperation:
        return self.root.operation


class PowerController(Protocol):
    """MCP-facing deterministic power controller contract."""

    @property
    def acceptance_context(self) -> PowerAcceptanceContext:
        raise NotImplementedError

    def read_state(self) -> DispenserPowerState:
        raise NotImplementedError

    def prepare(self) -> PowerActionResult:
        raise NotImplementedError

    def enable(self, *, confirmation: EnableConfirmation) -> PowerActionResult:
        raise NotImplementedError

    def set_current(
        self, *, target_current_a: float, expected_current_a: float
    ) -> PowerActionResult:
        raise NotImplementedError

    def shutdown(self) -> PowerActionResult:
        raise NotImplementedError


class PowerSafetyLimits(BaseModel):
    """Immutable startup policy visible to the model but not changeable by it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    control_enabled: bool
    acceptance_context: PowerAcceptanceContext
    required_enable_confirmation: EnableConfirmation
    fixed_compliance_voltage_v: float = Field(ge=0, le=32)
    operator_max_load_current_a: float = Field(gt=0, le=6.4)
    deployment_native_current_ceiling_a: float = Field(ge=2.4, le=2.4)
    deployment_commanded_load_current_ceiling_a: float = Field(ge=4.8, le=4.8)
    workflow_absolute_current_ceiling_a: float = Field(ge=4.8, le=4.8)
    topology_hardware_current_ceiling_a: float = Field(gt=0, le=6.4)
    upward_step_a: float = Field(gt=0, le=0.2)
    native_voltage_resolution_v: float = Field(gt=0)
    native_current_resolution_a: float = Field(gt=0)
    no_load_test_safe_measured_current_abs_a: float = Field(ge=0.001, le=0.001)


class NoLoadTestInterlockState(BaseModel):
    """Read-only diagnostic view of the process-local no-load test latch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    applicable: bool
    status: Literal[
        "not_applicable",
        "unlatched",
        "latched",
    ]
    trip: NoLoadTestTripRecord | None
    validation_status: Literal[
        "not_applicable",
        "offline_simulation_only_not_retested_on_physical_instrument",
    ]


class DispenserPowerState(BaseModel):
    """One fresh, explicit power-supply state snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime = Field(description="Collector timestamp in UTC.")
    source: Literal["siglent_spd3000.semantic_driver"]
    configured_topology: Literal["parallel_ch1"]
    load_current_factor: Literal[2]
    expected_operating_mode: Literal["parallel"]
    live_operating_mode: str
    topology_matches: bool
    selected_native_channel: Literal["CH1"]
    manufacturer: str
    model: str
    serial_number: str
    firmware_version: str
    native_voltage_setpoint_v: float = Field(ge=0)
    native_current_setpoint_a: float = Field(ge=0)
    commanded_load_current_limit_a: float = Field(ge=0)
    measured_native_channel_voltage_v: float
    measured_native_channel_current_a: float
    measured_native_channel_power_w: float | None
    output_enabled: bool
    regulation_mode: str
    compliance_voltage_matches: bool
    prepared_for_enable: bool
    no_load_test_interlock: NoLoadTestInterlockState
    safety_limits: PowerSafetyLimits
    driver_hardware_validation_status: Literal[
        "validated_on_physical_instrument_via_gateway"
    ]
    mcp_read_path_validation_status: Literal[
        "validated_on_physical_instrument_via_authenticated_gateway"
    ]
    mcp_actuation_validation_status: Literal[
        "not_yet_validated_with_connected_dispenser",
        "validated_on_unloaded_physical_instrument_via_authenticated_gateway",
    ]


def mcp_actuation_validation_status(
    acceptance_context: PowerAcceptanceContext,
) -> Literal[
    "not_yet_validated_with_connected_dispenser",
    "validated_on_unloaded_physical_instrument_via_authenticated_gateway",
]:
    """Return the physical validation status for one acceptance context."""

    if acceptance_context == "no_load_test":
        return MCP_NO_LOAD_TEST_ACTUATION_VALIDATION_STATUS
    return MCP_PRODUCTION_ACTUATION_VALIDATION_STATUS


class PowerActionResult(BaseModel):
    """Verified state after one bounded control action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal[
        "prepare_dispenser_power",
        "enable_dispenser_output",
        "set_dispenser_current",
        "shutdown_dispenser_power",
    ]
    wrote_hardware: bool
    state: DispenserPowerState


def required_enable_confirmation(
    acceptance_context: PowerAcceptanceContext,
) -> EnableConfirmation:
    """Return the one confirmation literal allowed by a startup context."""

    if acceptance_context == "production_dispenser":
        return PRODUCTION_PARALLEL_CONNECTION_CONFIRMATION
    return NO_LOAD_TEST_CONFIRMATION
