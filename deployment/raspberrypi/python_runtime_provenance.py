"""Record and verify Raspberry Pi OS CPython package/runtime identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

PACKAGES = (
    "libpython3.13-minimal",
    "libpython3.13-stdlib",
    "python3.13",
    "python3.13-minimal",
    "python3.13-venv",
)
SYSTEM_PYTHON = Path("/usr/bin/python3.13")
DPKG_QUERY = "/usr/bin/dpkg-query"


class RuntimeProvenanceError(RuntimeError):
    """Reject unsupported or drifted system CPython identity."""


def _fail(message: str) -> NoReturn:
    raise RuntimeProvenanceError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_inventory() -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for package in PACKAGES:
        completed = subprocess.run(
            [
                DPKG_QUERY,
                "-W",
                "-f=${Package}\t${Version}\t${Architecture}",
                package,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        fields = completed.stdout.strip().split("\t")
        if completed.returncode != 0 or len(fields) != 3 or fields[0] != package:
            _fail("A required CPython package identity is unavailable.")
        if fields[2] not in {"arm64", "all"}:
            _fail("A CPython package has the wrong architecture.")
        result.append(
            {"name": fields[0], "version": fields[1], "architecture": fields[2]}
        )
    return result


def current_manifest() -> dict[str, object]:
    """Return exact current system-Python and Debian package identity."""

    executable = Path(sys.executable).resolve(strict=True)
    if (
        os.name != "posix"
        or sys.platform != "linux"
        or platform.machine() != "aarch64"
        or sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 13)
        or executable != SYSTEM_PYTHON
    ):
        _fail("The runtime is not Linux aarch64 CPython 3.13.")
    owner = subprocess.run(
        [DPKG_QUERY, "--search", str(SYSTEM_PYTHON)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    owner_line = owner.stdout.strip()
    owner_name, separator, owner_path = owner_line.partition(": ")
    if (
        owner.returncode != 0
        or separator != ": "
        or owner_path != str(SYSTEM_PYTHON)
        or owner_name not in {"python3.13-minimal", "python3.13-minimal:arm64"}
    ):
        _fail("The system interpreter is not owned by python3.13-minimal.")
    return {
        "schema_version": 1,
        "target": "raspberry_pi_os_trixie_aarch64_cpython_3_13",
        "python_executable": str(executable),
        "python_executable_sha256": _sha256(executable),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
        "python_owner_package": owner_name,
        "dpkg_packages": _package_inventory(),
    }


def create(path: Path) -> None:
    if not path.is_absolute() or path.exists() or path.is_symlink():
        _fail("The runtime record output must be a new absolute file.")
    encoded = json.dumps(current_manifest(), indent=2, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def verify(path: Path, expected_sha256: str) -> None:
    if len(expected_sha256) != 64 or _sha256(path) != expected_sha256.lower():
        _fail("The approved runtime record hash does not match.")
    try:
        recorded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeProvenanceError("The runtime record is unreadable.") from error
    if recorded != current_manifest():
        _fail("The system CPython runtime identity has drifted.")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--output", required=True, type=Path)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", required=True, type=Path)
    verify_parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            create(args.output)
        else:
            verify(args.manifest, args.expected_manifest_sha256)
    except (RuntimeProvenanceError, OSError):
        print(
            "Raspberry Pi Python runtime provenance validation failed.", file=sys.stderr
        )
        return 1
    print("Raspberry Pi Python runtime provenance validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
