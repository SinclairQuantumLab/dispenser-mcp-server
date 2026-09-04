import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dispenser_conditioning_mcp import interlock
from dispenser_conditioning_mcp.interlock import (
    FileUnloadedHilDurableStateProvider,
    InterlockStorageError,
)
from dispenser_conditioning_mcp.power_domain import UnloadedHilTripRecord

STARTED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def initialize_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "record_type": "initialized_state",
                "schema_version": 1,
                "initialized_at": STARTED_AT.isoformat(),
            }
        ),
        encoding="utf-8",
    )


def trip_record() -> UnloadedHilTripRecord:
    return UnloadedHilTripRecord.legacy(
        observed_at=STARTED_AT,
        observed_native_channel_current_a=0.001,
        operation="enable_dispenser_output",
    )


def test_file_state_provider_persists_first_trip_without_reset_surface(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trip.json"
    initialize_state(path)
    provider = FileUnloadedHilDurableStateProvider(path)

    assert provider.read_trip() is None
    provider.record_trip(trip_record())

    restarted = FileUnloadedHilDurableStateProvider(path)
    assert restarted.read_trip() == trip_record()
    for forbidden in ("reset", "clear", "delete", "unlink"):
        assert not hasattr(provider, forbidden)


def test_missing_state_is_not_operator_initialized(tmp_path: Path) -> None:
    provider = FileUnloadedHilDurableStateProvider(tmp_path / "state.json")

    with pytest.raises(InterlockStorageError, match="not operator-initialized"):
        provider.read_state()
    with pytest.raises(InterlockStorageError, match="not operator-initialized"):
        provider.begin_operation(
            operation="prepare_dispenser_power",
            started_at=STARTED_AT,
        )


def test_initialized_state_can_be_replaced_by_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    initialize_state(path)
    provider = FileUnloadedHilDurableStateProvider(path)

    provider.record_trip(trip_record())

    assert provider.read_trip() == trip_record()


def test_posix_replace_syncs_parent_after_atomic_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "state.json"
    temporary = tmp_path / ".state.tmp"
    temporary.write_bytes(b"pending")
    provider = FileUnloadedHilDurableStateProvider(destination)
    events: list[str] = []

    def replace(source: Path, target: Path) -> None:
        assert source == temporary
        assert target == destination
        events.append("replace")

    monkeypatch.setattr(interlock.os, "replace", replace)
    monkeypatch.setattr(
        provider,
        "_fsync_parent_directory",
        lambda: events.append("fsync_parent"),
    )

    provider._replace_staged_file(temporary)  # pyright: ignore[reportPrivateUsage]

    assert events == ["replace", "fsync_parent"]


def test_parent_sync_failure_leaves_pending_record_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    initialize_state(path)
    provider = FileUnloadedHilDurableStateProvider(path)
    monkeypatch.setattr(
        provider,
        "_fsync_parent_directory",
        lambda: (_ for _ in ()).throw(OSError("directory sync unavailable")),
    )

    with pytest.raises(InterlockStorageError, match="could not be committed"):
        provider.begin_operation(
            operation="prepare_dispenser_power",
            started_at=STARTED_AT,
        )

    assert FileUnloadedHilDurableStateProvider(path).read_state().pending_operation


def test_pending_marker_survives_restart_until_safe_completion(tmp_path: Path) -> None:
    path = tmp_path / "trip.json"
    initialize_state(path)
    latch = FileUnloadedHilDurableStateProvider(path)

    pending = latch.begin_operation(
        operation="set_dispenser_current",
        started_at=STARTED_AT,
    )
    restarted_pending = FileUnloadedHilDurableStateProvider(path).read_state()

    assert restarted_pending.trip is None
    assert restarted_pending.pending_operation == pending

    latch.complete_operation(
        pending,
        completed_at=STARTED_AT + timedelta(seconds=1),
    )
    restarted_completed = FileUnloadedHilDurableStateProvider(path).read_state()
    assert restarted_completed.trip is None
    assert restarted_completed.pending_operation is None
    assert path.exists()


@pytest.mark.parametrize("current", [0.001, -0.001])
def test_v041_schema_v1_trip_remains_latched_without_reinterpretation(
    tmp_path: Path,
    current: float,
) -> None:
    path = tmp_path / "trip.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "observed_at": "2026-09-03T12:00:00Z",
                "observed_native_channel_current_a": current,
                "reason": "post_operation_nonzero_measured_native_current",
                "operation": "enable_dispenser_output",
            }
        ),
        encoding="utf-8",
    )

    record = FileUnloadedHilDurableStateProvider(path).read_trip()

    assert record is not None
    assert record.schema_version == 1
    assert record.observed_native_channel_current_a == current


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": 1,
            "observed_at": "2026-09-03T12:00:00Z",
            "observed_native_channel_current_a": 0.0,
            "reason": "post_operation_nonzero_measured_native_current",
            "operation": "enable_dispenser_output",
        },
        {
            "schema_version": 1,
            "observed_at": "2026-09-03T12:00:00Z",
            "observed_native_channel_current_a": float("nan"),
            "reason": "post_operation_nonzero_measured_native_current",
            "operation": "enable_dispenser_output",
        },
        {
            "schema_version": 1,
            "observed_at": "2026-09-03T12:00:00Z",
            "observed_native_channel_current_a": None,
            "reason": "post_operation_nonzero_measured_native_current",
            "operation": "enable_dispenser_output",
        },
        {
            "schema_version": 2,
            "observed_at": "2026-09-03T12:00:00Z",
            "observed_native_channel_current_a": 0.001,
            "reason": "post_operation_measured_native_current_outside_safe_band",
            "operation": "enable_dispenser_output",
        },
        {
            "schema_version": 2,
            "observed_at": "2026-09-03T12:00:00Z",
            "observed_native_channel_current_a": 0.002,
            "reason": "post_operation_measured_native_current_unavailable",
            "operation": "enable_dispenser_output",
        },
    ],
)
def test_invalid_trip_variants_fail_closed_in_model_and_storage(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        UnloadedHilTripRecord.model_validate(payload)

    path = tmp_path / "trip.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InterlockStorageError, match="invalid"):
        FileUnloadedHilDurableStateProvider(path).read_state()


def test_file_state_provider_rejects_corrupt_state_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "trip.json"
    path.write_text("{not-json", encoding="utf-8")
    provider = FileUnloadedHilDurableStateProvider(path)

    with pytest.raises(InterlockStorageError, match="invalid"):
        provider.read_trip()
    with pytest.raises(InterlockStorageError, match="invalid"):
        provider.record_trip(trip_record())


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics only")
def test_transient_windows_atomic_replace_lock_is_bounded_and_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    initialize_state(path)
    provider = FileUnloadedHilDurableStateProvider(path)
    pending = provider.begin_operation(
        operation="prepare_dispenser_power",
        started_at=STARTED_AT,
    )
    original_replace = interlock.os.replace
    replace_calls = 0
    delays: list[float] = []

    def transient_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls <= 2:
            raise PermissionError(13, "transient sharing conflict", str(path), 5)
        original_replace(source, destination)

    monkeypatch.setattr(interlock.os, "replace", transient_replace)
    monkeypatch.setattr(interlock.time, "sleep", delays.append)

    provider.complete_operation(
        pending,
        completed_at=STARTED_AT + timedelta(seconds=1),
    )

    assert replace_calls == 3
    assert delays == [0.005, 0.01]
    assert provider.read_state().pending_operation is None


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics only")
def test_exhausted_windows_atomic_replace_retry_preserves_pending_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    initialize_state(path)
    provider = FileUnloadedHilDurableStateProvider(path)
    pending = provider.begin_operation(
        operation="prepare_dispenser_power",
        started_at=STARTED_AT,
    )
    replace_calls = 0
    delays: list[float] = []

    def locked_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        del source, destination
        replace_calls += 1
        raise PermissionError(13, "persistent sharing conflict", str(path), 32)

    monkeypatch.setattr(interlock.os, "replace", locked_replace)
    monkeypatch.setattr(interlock.time, "sleep", delays.append)

    with pytest.raises(InterlockStorageError, match="could not be committed"):
        provider.complete_operation(
            pending,
            completed_at=STARTED_AT + timedelta(seconds=1),
        )

    assert replace_calls == 5
    assert delays == [0.005, 0.01, 0.02, 0.04]
    assert provider.read_state().pending_operation == pending
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics only")
def test_nonsharing_atomic_replace_error_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    initialize_state(path)
    provider = FileUnloadedHilDurableStateProvider(path)
    pending = provider.begin_operation(
        operation="prepare_dispenser_power",
        started_at=STARTED_AT,
    )
    replace_calls = 0
    delays: list[float] = []

    def invalid_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        del source, destination
        replace_calls += 1
        raise OSError(22, "non-transient replace failure", str(path), 87)

    monkeypatch.setattr(interlock.os, "replace", invalid_replace)
    monkeypatch.setattr(interlock.time, "sleep", delays.append)

    with pytest.raises(InterlockStorageError, match="could not be committed"):
        provider.complete_operation(
            pending,
            completed_at=STARTED_AT + timedelta(seconds=1),
        )

    assert replace_calls == 1
    assert delays == []
    assert provider.read_state().pending_operation == pending
