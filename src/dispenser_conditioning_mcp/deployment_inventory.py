"""Offline verification of the dedicated-host Python distribution inventory."""

from __future__ import annotations

import json
import platform
import re
import sys
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path
from typing import NotRequired, TypedDict, cast


class DistributionRecord(TypedDict):
    name: str
    version: str


class RuntimeInventory(TypedDict):
    schema_version: int
    platform: str
    machine: str
    python_major: int
    python_minor: int
    distributions: list[DistributionRecord]
    description: NotRequired[str]


class InventoryValidationError(ValueError):
    """Raised when the installed interpreter does not match the reviewed inventory."""


_INVENTORY_KEYS = {
    "schema_version",
    "platform",
    "machine",
    "python_major",
    "python_minor",
    "distributions",
    "description",
}
_DISTRIBUTION_KEYS = {"name", "version"}


def normalize_distribution_name(name: str) -> str:
    """Return the PEP 503 normalized distribution name."""

    return re.sub(r"[-_.]+", "-", name).lower()


def load_runtime_inventory(path: Path) -> RuntimeInventory:
    """Load and strictly validate a reviewed runtime-inventory document."""

    try:
        raw_object: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryValidationError("Runtime inventory could not be read.") from exc

    if not isinstance(raw_object, dict):
        raise InventoryValidationError(
            "Runtime inventory has an invalid document shape."
        )
    raw = cast(dict[str, object], raw_object)
    if set(raw) != _INVENTORY_KEYS:
        raise InventoryValidationError(
            "Runtime inventory has an invalid document shape."
        )
    if (
        not isinstance(raw["schema_version"], int)
        or isinstance(raw["schema_version"], bool)
        or raw["schema_version"] != 1
    ):
        raise InventoryValidationError(
            "Runtime inventory schema version is unsupported."
        )
    supported_targets = {("win32", "amd64"), ("linux", "aarch64")}
    platform_name = raw["platform"]
    machine_name = raw["machine"]
    if (
        not isinstance(platform_name, str)
        or not isinstance(machine_name, str)
        or (platform_name, machine_name.lower()) not in supported_targets
    ):
        raise InventoryValidationError(
            "Runtime inventory targets an unsupported platform."
        )
    if (
        not isinstance(raw["python_major"], int)
        or isinstance(raw["python_major"], bool)
        or not isinstance(raw["python_minor"], int)
        or isinstance(raw["python_minor"], bool)
        or raw["python_major"] != 3
        or raw["python_minor"] != 13
    ):
        raise InventoryValidationError(
            "Runtime inventory targets an unsupported Python."
        )
    if not isinstance(raw["description"], str) or not raw["description"]:
        raise InventoryValidationError("Runtime inventory description is invalid.")

    distributions_object = raw["distributions"]
    if not isinstance(distributions_object, list) or not distributions_object:
        raise InventoryValidationError(
            "Runtime inventory distribution list is invalid."
        )
    distributions = cast(list[object], distributions_object)
    checked: list[DistributionRecord] = []
    seen: set[str] = set()
    for item_object in distributions:
        if not isinstance(item_object, dict):
            raise InventoryValidationError("Runtime inventory entry is invalid.")
        item = cast(dict[str, object], item_object)
        if set(item) != _DISTRIBUTION_KEYS:
            raise InventoryValidationError("Runtime inventory entry is invalid.")
        name = item["name"]
        version = item["version"]
        if not isinstance(name, str) or normalize_distribution_name(name) != name:
            raise InventoryValidationError("Runtime inventory name is not normalized.")
        if not isinstance(version, str) or not version:
            raise InventoryValidationError("Runtime inventory version is invalid.")
        if name in seen:
            raise InventoryValidationError(
                "Runtime inventory contains a duplicate name."
            )
        seen.add(name)
        checked.append({"name": name, "version": version})
    if [item["name"] for item in checked] != sorted(seen):
        raise InventoryValidationError("Runtime inventory is not sorted by name.")

    return {
        "schema_version": 1,
        "platform": platform_name,
        "machine": machine_name,
        "python_major": 3,
        "python_minor": 13,
        "distributions": checked,
        "description": raw["description"],
    }


def installed_distributions(
    source: Iterable[metadata.Distribution] | None = None,
) -> dict[str, str]:
    """Return the exact normalized distribution inventory for an interpreter."""

    result: dict[str, str] = {}
    for distribution in source if source is not None else metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise InventoryValidationError("An installed distribution has no name.")
        name = normalize_distribution_name(raw_name)
        if name in result:
            raise InventoryValidationError(
                "Installed inventory contains a duplicate name."
            )
        result[name] = distribution.version
    return result


def validate_runtime_inventory(
    expected: RuntimeInventory,
    actual: dict[str, str],
    *,
    platform_name: str = sys.platform,
    machine: str = platform.machine(),
    python_version: tuple[int, int] = (sys.version_info.major, sys.version_info.minor),
) -> None:
    """Reject platform, missing, extra, or version-drifted installations."""

    if platform_name != expected["platform"]:
        raise InventoryValidationError("Installed runtime platform does not match.")
    if machine.lower() != expected["machine"].lower():
        raise InventoryValidationError("Installed runtime machine does not match.")
    if python_version != (expected["python_major"], expected["python_minor"]):
        raise InventoryValidationError("Installed Python version does not match.")

    expected_distributions = {
        item["name"]: item["version"] for item in expected["distributions"]
    }
    if actual != expected_distributions:
        raise InventoryValidationError(
            "Installed distribution inventory does not match."
        )


def main() -> int:
    """Validate an installed dedicated-host interpreter without leaking paths."""

    if len(sys.argv) != 2:
        print("Runtime inventory validation failed.", file=sys.stderr)
        return 2
    try:
        expected = load_runtime_inventory(Path(sys.argv[1]))
        validate_runtime_inventory(expected, installed_distributions())
    except (InventoryValidationError, OSError):
        print("Runtime inventory validation failed.", file=sys.stderr)
        return 1
    print("Installed Python inventory validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
