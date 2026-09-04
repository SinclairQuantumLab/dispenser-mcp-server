"""Durable write-ahead operation state and reset-free unloaded-HIL trip storage."""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from dispenser_conditioning_mcp.power_domain import (
    PowerMutationOperation,
    UnloadedHilDurableState,
    UnloadedHilDurableStateProvider,
    UnloadedHilPendingOperationRecord,
    UnloadedHilTripRecord,
)

_WINDOWS_TRANSIENT_REPLACE_WINERRORS = frozenset({5, 32, 33})
_WINDOWS_REPLACE_RETRY_DELAYS_S = (0.005, 0.010, 0.020, 0.040)


class InterlockStorageError(RuntimeError):
    """Indicate that durable interlock state cannot be trusted."""


class _CompletedOperationRecord(BaseModel):
    """Durable commit that supersedes one safely completed pending operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_type: Literal["completed_operation"]
    schema_version: Literal[1]
    operation_id: str
    started_at: datetime
    completed_at: datetime
    operation: PowerMutationOperation


class _InitializedStateRecord(BaseModel):
    """Operator-created bootstrap record; the MCP cannot create or reset it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_type: Literal["initialized_state"]
    schema_version: Literal[1]
    initialized_at: datetime


_StoredRecord = (
    _InitializedStateRecord
    | UnloadedHilTripRecord
    | UnloadedHilPendingOperationRecord
    | _CompletedOperationRecord
)


class FileUnloadedHilDurableStateProvider(UnloadedHilDurableStateProvider):
    """Persist one trip-or-operation state file without a reset method."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def read_state(self) -> UnloadedHilDurableState:
        """Return the active trip/pending state or fail closed on invalid content."""

        record = self._read_record()
        if isinstance(record, UnloadedHilTripRecord):
            return UnloadedHilDurableState(trip=record, pending_operation=None)
        if isinstance(record, UnloadedHilPendingOperationRecord):
            return UnloadedHilDurableState(trip=None, pending_operation=record)
        return UnloadedHilDurableState(trip=None, pending_operation=None)

    def read_trip(self) -> UnloadedHilTripRecord | None:
        """Compatibility helper returning only a durable trip record."""

        return self.read_state().trip

    def begin_operation(
        self,
        *,
        operation: PowerMutationOperation,
        started_at: datetime,
    ) -> UnloadedHilPendingOperationRecord:
        """Commit a pending marker before the caller opens a device session."""

        state = self.read_state()
        if state.trip is not None:
            raise InterlockStorageError("The unloaded-HIL interlock is latched.")
        if state.pending_operation is not None:
            raise InterlockStorageError(
                "An unfinished unloaded-HIL power operation is already pending."
            )
        pending = UnloadedHilPendingOperationRecord(
            record_type="pending_operation",
            schema_version=1,
            operation_id=uuid4(),
            started_at=started_at,
            operation=operation,
        )
        self._replace_record(pending)
        verified = self._read_record()
        if verified != pending:
            raise InterlockStorageError(
                "The unloaded-HIL pending operation could not be verified."
            )
        return pending

    def complete_operation(
        self,
        pending: UnloadedHilPendingOperationRecord,
        *,
        completed_at: datetime,
    ) -> None:
        """Atomically supersede one pending marker after safe completion."""

        active = self._read_record()
        if active != pending:
            raise InterlockStorageError(
                "The unloaded-HIL pending operation changed before completion."
            )
        completed = _CompletedOperationRecord(
            record_type="completed_operation",
            schema_version=1,
            operation_id=str(pending.operation_id),
            started_at=pending.started_at,
            completed_at=completed_at,
            operation=pending.operation,
        )
        self._replace_record(completed)

    def record_trip(self, record: UnloadedHilTripRecord) -> None:
        """Replace a pending marker with the first structurally valid trip."""

        active = self._read_record()
        if isinstance(active, UnloadedHilTripRecord):
            return
        if isinstance(active, _CompletedOperationRecord):
            raise InterlockStorageError(
                "A completed unloaded-HIL operation cannot be replaced by a trip."
            )
        self._replace_record(record)

    def _read_record(self) -> _StoredRecord:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise InterlockStorageError(
                "The unloaded-HIL durable state is not operator-initialized."
            ) from error
        except OSError as error:
            raise InterlockStorageError(
                "The unloaded-HIL durable state is unreadable."
            ) from error
        try:
            untyped_payload: object = json.loads(raw)
            if not isinstance(untyped_payload, dict):
                raise ValueError("durable state must be a JSON object")
            payload = cast(dict[str, object], untyped_payload)
            record_type = payload.get("record_type")
            if record_type == "initialized_state":
                return _InitializedStateRecord.model_validate(payload)
            if record_type == "pending_operation":
                return UnloadedHilPendingOperationRecord.model_validate(payload)
            if record_type == "completed_operation":
                return _CompletedOperationRecord.model_validate(payload)
            return UnloadedHilTripRecord.model_validate(payload)
        except (ValueError, TypeError) as error:
            raise InterlockStorageError(
                "The unloaded-HIL durable state is invalid."
            ) from error

    def _replace_record(self, record: BaseModel) -> None:
        encoded = record.model_dump_json(indent=2).encode("utf-8") + b"\n"
        temporary_path = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
        except OSError as error:
            raise InterlockStorageError(
                "The unloaded-HIL durable state could not be staged."
            ) from error
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_staged_file(temporary_path)
        except OSError as error:
            raise InterlockStorageError(
                "The unloaded-HIL durable state could not be committed."
            ) from error
        finally:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)

    def _replace_staged_file(self, temporary_path: Path) -> None:
        """Retry only bounded transient Windows sharing conflicts."""

        remaining_delays = iter(_WINDOWS_REPLACE_RETRY_DELAYS_S)
        while True:
            try:
                os.replace(temporary_path, self._path)
                self._fsync_parent_directory()
                return
            except OSError as error:
                if not _is_transient_windows_replace_error(error):
                    raise
                try:
                    delay_s = next(remaining_delays)
                except StopIteration:
                    raise error from None
                time.sleep(delay_s)

    def _fsync_parent_directory(self) -> None:
        """Persist a POSIX rename in its local parent directory."""

        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self._path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _is_transient_windows_replace_error(error: OSError) -> bool:
    """Identify only access/share/lock failures from Windows atomic replace."""

    return (
        os.name == "nt"
        and getattr(error, "winerror", None) in _WINDOWS_TRANSIENT_REPLACE_WINERRORS
    )
