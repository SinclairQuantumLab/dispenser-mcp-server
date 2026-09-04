"""Validate HIL/production process separation before systemd startup."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


class SeparationError(RuntimeError):
    """Reject overlapping HIL and production process ownership."""


def _profile(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise SeparationError("A protected profile is unavailable.") from error
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SeparationError("A profile line is invalid.")
        name, value = line.split("=", 1)
        if not name or not value or name in values:
            raise SeparationError("A profile setting is invalid.")
        values[name] = value
    return values


def _service_user(path: Path) -> str:
    try:
        users = [
            line.removeprefix("User=")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("User=")
        ]
    except OSError as error:
        raise SeparationError("A systemd unit is unavailable.") from error
    if len(users) != 1 or not users[0]:
        raise SeparationError("A systemd unit has an invalid service user.")
    return users[0]


def validate(
    hil_path: Path, production_path: Path, hil_unit: Path, production_unit: Path
) -> None:
    """Require distinct unit, transport, credential, and physical-unit identity."""

    if hil_path.resolve() == production_path.resolve():
        raise SeparationError("HIL and production profiles are not distinct.")
    hil = _profile(hil_path)
    production = _profile(production_path)
    if hil.get("DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT") != "unloaded_hil":
        raise SeparationError("The HIL acceptance context is invalid.")
    if production.get("DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT") != "production_dispenser":
        raise SeparationError("The production acceptance context is invalid.")
    if (
        hil.get("DISPENSER_SIGLENT_CONTROL_ENABLED") != "false"
        or production.get("DISPENSER_SIGLENT_CONTROL_ENABLED") != "false"
    ):
        raise SeparationError("Initial commissioning profiles must disable control.")
    distinct_keys = (
        "DISPENSER_MCP_HTTP_PORT",
        "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE",
    )
    if any(
        not hil.get(key) or hil.get(key) == production.get(key) for key in distinct_keys
    ):
        raise SeparationError("HIL and production process boundaries overlap.")
    identifier_key = "DISPENSER_SIGLENT_IDENTIFIER"
    serial_key = "DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER"
    if (
        not hil.get(identifier_key)
        or not production.get(identifier_key)
        or hil[identifier_key] == production[identifier_key]
    ):
        raise SeparationError("HIL and production Siglent identifiers overlap.")
    if (
        not hil.get(serial_key)
        or not production.get(serial_key)
        or hil[serial_key] == production[serial_key]
    ):
        raise SeparationError("HIL and production expected serial numbers overlap.")
    if _service_user(hil_unit) == _service_user(production_unit):
        raise SeparationError("HIL and production service users overlap.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hil-profile", required=True, type=Path)
    parser.add_argument("--production-profile", required=True, type=Path)
    parser.add_argument("--hil-unit", required=True, type=Path)
    parser.add_argument("--production-unit", required=True, type=Path)
    args = parser.parse_args()
    try:
        validate(
            args.hil_profile,
            args.production_profile,
            args.hil_unit,
            args.production_unit,
        )
    except SeparationError:
        print("Raspberry Pi instance separation validation failed.", file=sys.stderr)
        return 1
    print("Raspberry Pi instance separation validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
