from __future__ import annotations

import json
from pathlib import Path

import pytest

from dispenser_conditioning_mcp import deployment_inventory as inventory


def write_inventory(path: Path, *, machine: str = "aarch64") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": "linux",
                "machine": machine,
                "python_major": 3,
                "python_minor": 13,
                "distributions": [{"name": "example", "version": "1.0"}],
                "description": "offline test",
            }
        ),
        encoding="utf-8",
    )


def test_linux_aarch64_inventory_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "inventory.json"
    write_inventory(path)

    expected = inventory.load_runtime_inventory(path)
    inventory.validate_runtime_inventory(
        expected,
        {"example": "1.0"},
        platform_name="linux",
        machine="aarch64",
        python_version=(3, 13),
    )


def test_non_aarch64_linux_inventory_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "inventory.json"
    write_inventory(path, machine="x86_64")

    with pytest.raises(
        inventory.InventoryValidationError, match="unsupported platform"
    ):
        inventory.load_runtime_inventory(path)
