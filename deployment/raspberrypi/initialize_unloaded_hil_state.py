"""Create one operator-authorized unloaded-HIL initialization record."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

CONFIRMATION = "confirmed_outputs_off_and_no_unapproved_load"


class InitializationError(RuntimeError):
    """Reject an unsafe or ambiguous operator initialization."""


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def initialize(path: Path, service_user: str, confirmation: str) -> None:
    """Create, fsync, and verify an initialization record without overwrite."""

    if os.name != "posix" or os.geteuid() != 0:
        raise InitializationError("Initialization requires a POSIX root operator.")
    if confirmation != CONFIRMATION:
        raise InitializationError("Fresh physical verification is required.")
    if not path.is_absolute() or path.name != "operation-state.json":
        raise InitializationError("State path must be the absolute approved filename.")
    try:
        account = pwd.getpwnam(service_user)
        parent_stat = path.parent.lstat()
    except (KeyError, OSError) as error:
        raise InitializationError(
            "Service identity or state directory is unavailable."
        ) from error
    if not stat.S_ISDIR(parent_stat.st_mode) or path.parent.is_symlink():
        raise InitializationError("State parent must be a real local directory.")
    if stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise InitializationError("State parent mode must be exactly 0700.")
    if (parent_stat.st_uid, parent_stat.st_gid) != (account.pw_uid, account.pw_gid):
        raise InitializationError(
            "State parent ownership does not match the service user."
        )
    if path.exists() or path.is_symlink():
        raise InitializationError(
            "Durable state already exists and is never overwritten."
        )

    record = {
        "record_type": "initialized_state",
        "schema_version": 1,
        "initialized_at": datetime.now(UTC).isoformat(),
    }
    encoded = json.dumps(record, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    descriptor = -1
    try:
        # Publish directly with O_EXCL: a concurrent creator can never be
        # overwritten. A crash may leave a partial record, which the MCP treats
        # as invalid and fail-closed until an out-of-band physical review.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchown(descriptor, account.pw_uid, account.pw_gid)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(path.parent)
        if path.read_bytes() != encoded:
            raise InitializationError("Initialized state could not be verified.")
    except OSError as error:
        raise InitializationError(
            "Initialized state could not be committed."
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--physical-verification", required=True)
    args = parser.parse_args()
    try:
        initialize(args.state_file, args.service_user, args.physical_verification)
    except InitializationError:
        print("Unloaded-HIL state initialization failed.", file=sys.stderr)
        return 1
    print("Unloaded-HIL state initialization passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
