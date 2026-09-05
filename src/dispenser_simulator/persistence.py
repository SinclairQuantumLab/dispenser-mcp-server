"""Operator-owned storage for the unloaded-HIL simulator interlock."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .metadata import UNLOADED_HIL_SAFE_MEASURED_CURRENT_ABS_A

_FILE_SCHEMA_VERSION = 2
_READABLE_FILE_SCHEMA_VERSIONS = frozenset({1, 2})
_MAX_FILE_BYTES = 65_536
_REPLACE_RETRY_DELAYS_S = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
_FAIL_CLOSED_REASONS = frozenset(
    {"persistence_unavailable", "unfinished_pending_operation"}
)
_TRIP_FIELDS = {
    "schema_version",
    "observed_at",
    "observed_native_ch1_current_a",
    "reason",
    "mutating_operation",
}


class InterlockStateError(RuntimeError):
    """A sanitized unloaded-HIL interlock state-file error."""


@dataclass(frozen=True)
class UnloadedHilInterlockSnapshot:
    """The durable portion of the unloaded-HIL interlock state."""

    status: str
    trip: dict[str, Any] | None
    failure_reason: str | None


class UnloadedHilInterlockStore(Protocol):
    """Storage boundary required by an unloaded-HIL simulator runtime."""

    def load(self) -> UnloadedHilInterlockSnapshot: ...

    def save(self, snapshot: UnloadedHilInterlockSnapshot) -> None: ...


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _require_trip_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TRIP_FIELDS:
        raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
    schema_version = value["schema_version"]
    if type(schema_version) is not int:
        raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
    if not isinstance(value["observed_at"], str) or not value["observed_at"]:
        raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
    if (
        not isinstance(value["mutating_operation"], str)
        or not value["mutating_operation"]
    ):
        raise InterlockStateError("Unloaded-HIL interlock state is invalid.")

    observed = value["observed_native_ch1_current_a"]
    reason = value["reason"]
    if schema_version == 1:
        valid = (
            reason == "post_operation_nonzero_measured_native_current"
            and _is_finite_number(observed)
            and observed != 0.0
        )
    elif schema_version == 2 and reason == (
        "post_operation_measured_native_current_outside_safe_band"
    ):
        valid = _is_finite_number(observed) and (
            abs(observed) > UNLOADED_HIL_SAFE_MEASURED_CURRENT_ABS_A
        )
    elif (
        schema_version == 2
        and reason == "post_operation_measured_native_current_unavailable"
    ):
        valid = observed is None
    else:
        valid = False
    if not valid:
        raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
    return copy.deepcopy(value)


def _require_snapshot(
    value: object,
    *,
    file_schema_version: int = _FILE_SCHEMA_VERSION,
) -> UnloadedHilInterlockSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "trip",
        "failure_reason",
    }:
        raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
    status = value["status"]
    if status == "unlatched":
        if value["trip"] is not None or value["failure_reason"] is not None:
            raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        return UnloadedHilInterlockSnapshot(
            status="unlatched", trip=None, failure_reason=None
        )
    if status == "latched":
        trip = _require_trip_record(value["trip"])
        failure_reason = value["failure_reason"]
        if file_schema_version == 1:
            # Simulator 0.2.1 wrote the trip reason into failure_reason. Read
            # that legacy file shape, but normalize it to the public v0.4.3
            # invariant exposed by all current results and writes.
            if failure_reason not in {None, trip["reason"]}:
                raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        elif failure_reason is not None:
            raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        return UnloadedHilInterlockSnapshot(
            status="latched",
            trip=trip,
            failure_reason=None,
        )
    if status == "unavailable_fail_closed":
        if file_schema_version < 2:
            raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        failure_reason = value["failure_reason"]
        if value["trip"] is not None or failure_reason not in _FAIL_CLOSED_REASONS:
            raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        return UnloadedHilInterlockSnapshot(
            status="unavailable_fail_closed",
            trip=None,
            failure_reason=failure_reason,
        )
    else:
        raise InterlockStateError("Unloaded-HIL interlock state is invalid.")


def _handle_digest(run_handle: str) -> str:
    return hashlib.sha256(
        f"dispenser-simulator-unloaded-hil-v1\0{run_handle}".encode()
    ).hexdigest()


def _reject_non_json_number(token: str) -> None:
    raise ValueError(f"Non-JSON number {token!r}")


def _atomic_replace(source: str, destination: Path) -> None:
    """Replace once atomically, tolerating bounded transient sharing locks."""

    for delay_s in (*_REPLACE_RETRY_DELAYS_S, None):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if delay_s is None:
                raise
            time.sleep(delay_s)


class MemoryUnloadedHilInterlockStore:
    """In-process store used only by domain tests and harnesses."""

    def __init__(self) -> None:
        self._snapshot = UnloadedHilInterlockSnapshot(
            status="unlatched", trip=None, failure_reason=None
        )

    def load(self) -> UnloadedHilInterlockSnapshot:
        return copy.deepcopy(self._snapshot)

    def save(self, snapshot: UnloadedHilInterlockSnapshot) -> None:
        self._snapshot = _require_snapshot(
            {
                "status": snapshot.status,
                "trip": snapshot.trip,
                "failure_reason": snapshot.failure_reason,
            }
        )


class FileUnloadedHilInterlockStore:
    """Atomic JSON-file store bound to one operator-selected run handle."""

    def __init__(self, state_path: str, run_handle: str):
        if not isinstance(state_path, str) or not state_path:
            raise InterlockStateError("Unloaded-HIL interlock state is required.")
        if not isinstance(run_handle, str) or not run_handle:
            raise InterlockStateError("Unloaded-HIL interlock state is required.")
        path = Path(state_path)
        if not path.is_absolute():
            raise InterlockStateError(
                "Unloaded-HIL interlock state path must be absolute."
            )
        self._path = path
        self._binding_sha256 = _handle_digest(run_handle)

    def _document(self, snapshot: UnloadedHilInterlockSnapshot) -> dict[str, Any]:
        checked = _require_snapshot(
            {
                "status": snapshot.status,
                "trip": snapshot.trip,
                "failure_reason": snapshot.failure_reason,
            }
        )
        return {
            "schema_version": _FILE_SCHEMA_VERSION,
            "binding_sha256": self._binding_sha256,
            "unloaded_hil_interlock": {
                "status": checked.status,
                "trip": checked.trip,
                "failure_reason": checked.failure_reason,
            },
        }

    def initialize(self) -> None:
        document = self._document(
            UnloadedHilInterlockSnapshot(
                status="unlatched", trip=None, failure_reason=None
            )
        )
        try:
            payload = json.dumps(
                document,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            with self._path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError):
            raise InterlockStateError(
                "Unloaded-HIL interlock state initialization failed."
            ) from None

    def load(self) -> UnloadedHilInterlockSnapshot:
        try:
            if not self._path.is_file() or self._path.stat().st_size > _MAX_FILE_BYTES:
                raise OSError
            document = json.loads(
                self._path.read_text(encoding="utf-8"),
                parse_constant=_reject_non_json_number,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            raise InterlockStateError(
                "Unloaded-HIL interlock state is unavailable or invalid."
            ) from None
        if not isinstance(document, dict) or set(document) != {
            "schema_version",
            "binding_sha256",
            "unloaded_hil_interlock",
        }:
            raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        file_schema_version = document["schema_version"]
        if (
            type(file_schema_version) is not int
            or file_schema_version not in _READABLE_FILE_SCHEMA_VERSIONS
        ):
            raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        binding = document["binding_sha256"]
        if not isinstance(binding, str) or not hmac.compare_digest(
            binding, self._binding_sha256
        ):
            raise InterlockStateError("Unloaded-HIL interlock state is invalid.")
        return _require_snapshot(
            document["unloaded_hil_interlock"],
            file_schema_version=file_schema_version,
        )

    def save(self, snapshot: UnloadedHilInterlockSnapshot) -> None:
        document = self._document(snapshot)
        temporary_path: str | None = None
        try:
            payload = json.dumps(
                document,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                dir=self._path.parent,
                text=True,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            _atomic_replace(temporary_path, self._path)
            temporary_path = None
        except (OSError, TypeError, ValueError):
            raise InterlockStateError(
                "Unloaded-HIL interlock state could not be written; control remains fail-closed."
            ) from None
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass


def initialize_unloaded_hil_interlock_file(state_path: str, run_handle: str) -> None:
    """Create one unlatched state file without overwriting an existing run."""

    FileUnloadedHilInterlockStore(state_path, run_handle).initialize()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize a new unloaded-HIL simulator interlock state file."
    )
    parser.add_argument("command", choices=["initialize"])
    parser.parse_args()
    state_path = os.environ.get("DISPENSER_SIM_UNLOADED_HIL_STATE_PATH", "")
    run_handle = os.environ.get("DISPENSER_SIM_UNLOADED_HIL_RUN_HANDLE", "")
    try:
        initialize_unloaded_hil_interlock_file(state_path, run_handle)
    except InterlockStateError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    print("Unloaded-HIL simulator interlock state initialized.")


if __name__ == "__main__":
    main()
