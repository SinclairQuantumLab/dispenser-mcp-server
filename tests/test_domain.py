from datetime import UTC, datetime, timedelta, timezone

import pytest

from dispenser_conditioning_mcp.domain import (
    MBAR_TO_TORR,
    SOURCE_LABEL,
    PressureObservationError,
    RawPressureObservation,
    normalize_observation,
)


def test_normalize_observation_converts_units_and_utc() -> None:
    observation = normalize_observation(
        RawPressureObservation(
            observed_at=datetime(
                2026, 9, 3, 7, 8, 9, tzinfo=timezone(timedelta(hours=-5))
            ),
            pressure_mbar=1.25e-7,
            p1_drive_serial_number="  TC80-123  ",
        )
    )

    assert observation.observed_at == datetime(2026, 9, 3, 12, 8, 9, tzinfo=UTC)
    assert observation.pressure_mbar == 1.25e-7
    assert observation.pressure_torr == pytest.approx(1.25e-7 * MBAR_TO_TORR)
    assert observation.source == SOURCE_LABEL
    assert observation.p1_drive_serial_number == "TC80-123"
    assert observation.is_total_gauge_pressure is True
    assert observation.is_rubidium_partial_pressure is False
    assert observation.verifies_dispenser_activation is False


@pytest.mark.parametrize("pressure", [0.0, -1.0, float("inf"), float("nan")])
def test_normalize_observation_rejects_invalid_pressure(pressure: float) -> None:
    with pytest.raises(PressureObservationError):
        normalize_observation(
            RawPressureObservation(
                observed_at=datetime(2026, 9, 3, tzinfo=UTC),
                pressure_mbar=pressure,
                p1_drive_serial_number="TC80-123",
            )
        )


def test_normalize_observation_rejects_naive_timestamp() -> None:
    with pytest.raises(PressureObservationError):
        normalize_observation(
            RawPressureObservation(
                observed_at=datetime(2026, 9, 3),
                pressure_mbar=1e-7,
                p1_drive_serial_number="TC80-123",
            )
        )
