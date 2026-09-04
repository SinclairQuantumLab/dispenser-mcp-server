"""Domain contracts and normalization for vacuum pressure observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

MBAR_TO_TORR = 760.0 / 1013.25
SOURCE_LABEL = "pfeiffer_hicube_neo.pvviewer.g1_pressure"


class PressureObservationError(RuntimeError):
    """Report that a valid pressure observation is unavailable."""


@dataclass(frozen=True)
class RawPressureObservation:
    """Pressure fields read from one normalized HiCube Neo batch snapshot."""

    observed_at: datetime
    pressure_mbar: float
    p1_drive_serial_number: str


class PressureObservationSource(Protocol):
    """Supply one read-only pressure observation."""

    def read(self) -> RawPressureObservation:
        """Return one observation or raise `PressureObservationError`."""

        raise NotImplementedError


class VacuumPressureObservation(BaseModel):
    """One validated total-pressure observation from the HiCube Neo G1 channel."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime = Field(
        description="Collector timestamp in UTC for the completed batch read."
    )
    pressure_mbar: float = Field(
        gt=0,
        description="Native G1 total gauge pressure reported by PVViewer, in mbar.",
    )
    pressure_torr: float = Field(
        gt=0,
        description="Derived total gauge pressure in Torr using mbar * 760 / 1013.25.",
    )
    source: Literal["pfeiffer_hicube_neo.pvviewer.g1_pressure"] = Field(
        description="Unambiguous instrument channel and source attribute label."
    )
    p1_drive_serial_number: str = Field(
        min_length=1,
        description=(
            "P1/TC 80 electronic-drive serial used as source identity; this is not "
            "a gauge or station serial number."
        ),
    )
    is_total_gauge_pressure: Literal[True] = Field(
        description="Always true: this observation is total gauge pressure."
    )
    is_rubidium_partial_pressure: Literal[False] = Field(
        description="Always false: the G1 reading is not rubidium partial pressure."
    )
    verifies_dispenser_activation: Literal[False] = Field(
        description=(
            "Always false: this pressure reading does not verify dispenser activation "
            "or function."
        )
    )


def normalize_observation(
    observation: RawPressureObservation,
) -> VacuumPressureObservation:
    """Validate one source observation and derive its Torr representation."""

    observed_at = observation.observed_at
    if observed_at.utcoffset() is None:
        raise PressureObservationError("The source timestamp is not timezone-aware.")

    pressure_mbar = observation.pressure_mbar
    if not math.isfinite(pressure_mbar) or pressure_mbar <= 0:
        raise PressureObservationError(
            "The source pressure is not finite and positive."
        )

    serial_number = observation.p1_drive_serial_number.strip()
    if not serial_number:
        raise PressureObservationError("The P1 drive serial number is empty.")

    return VacuumPressureObservation(
        observed_at=observed_at.astimezone(UTC),
        pressure_mbar=pressure_mbar,
        pressure_torr=pressure_mbar * MBAR_TO_TORR,
        source=SOURCE_LABEL,
        p1_drive_serial_number=serial_number,
        is_total_gauge_pressure=True,
        is_rubidium_partial_pressure=False,
        verifies_dispenser_activation=False,
    )
