from __future__ import annotations

import json
from pathlib import Path

import pytest

from dispenser_conditioning_mcp.deployment_inventory import (
    InventoryValidationError,
    load_runtime_inventory,
    validate_runtime_inventory,
)

PROJECT_ROOT = Path(__file__).parents[1]
INVENTORY = PROJECT_ROOT / "deployment" / "windows" / "python-runtime-inventory.json"


def _actual_from_inventory() -> dict[str, str]:
    expected = load_runtime_inventory(INVENTORY)
    return {item["name"]: item["version"] for item in expected["distributions"]}


def test_runtime_inventory_is_closed_sorted_and_exact() -> None:
    expected = load_runtime_inventory(INVENTORY)

    assert expected["schema_version"] == 1
    assert expected["platform"] == "win32"
    assert expected["machine"] == "AMD64"
    assert (expected["python_major"], expected["python_minor"]) == (3, 13)
    assert expected["distributions"] == sorted(
        expected["distributions"], key=lambda item: item["name"]
    )
    assert {item["name"] for item in expected["distributions"]} == set(
        _actual_from_inventory()
    )


def test_runtime_inventory_accepts_only_exact_installation() -> None:
    expected = load_runtime_inventory(INVENTORY)

    validate_runtime_inventory(
        expected,
        _actual_from_inventory(),
        platform_name="win32",
        machine="amd64",
        python_version=(3, 13),
    )


@pytest.mark.parametrize("defect", ["missing", "extra", "version"])
def test_runtime_inventory_rejects_distribution_drift(defect: str) -> None:
    expected = load_runtime_inventory(INVENTORY)
    actual = _actual_from_inventory()
    if defect == "missing":
        actual.pop("mcp")
    elif defect == "extra":
        actual["unreviewed-package"] = "1.0"
    else:
        actual["mcp"] = "999"

    with pytest.raises(
        InventoryValidationError,
        match="Installed distribution inventory does not match",
    ):
        validate_runtime_inventory(
            expected,
            actual,
            platform_name="win32",
            machine="AMD64",
            python_version=(3, 13),
        )


@pytest.mark.parametrize(
    ("platform_name", "machine", "python_version"),
    [
        ("linux", "AMD64", (3, 13)),
        ("win32", "ARM64", (3, 13)),
        ("win32", "AMD64", (3, 14)),
    ],
)
def test_runtime_inventory_rejects_platform_drift(
    platform_name: str,
    machine: str,
    python_version: tuple[int, int],
) -> None:
    expected = load_runtime_inventory(INVENTORY)

    with pytest.raises(InventoryValidationError):
        validate_runtime_inventory(
            expected,
            _actual_from_inventory(),
            platform_name=platform_name,
            machine=machine,
            python_version=python_version,
        )


def test_runtime_inventory_rejects_unknown_document_field(tmp_path: Path) -> None:
    raw = json.loads(INVENTORY.read_text(encoding="utf-8"))
    raw["unreviewed"] = True
    candidate = tmp_path / "inventory.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(InventoryValidationError, match="invalid document shape"):
        load_runtime_inventory(candidate)


def test_runtime_inventory_rejects_boolean_schema_version(tmp_path: Path) -> None:
    raw = json.loads(INVENTORY.read_text(encoding="utf-8"))
    raw["schema_version"] = True
    candidate = tmp_path / "inventory.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(InventoryValidationError, match="schema version"):
        load_runtime_inventory(candidate)
