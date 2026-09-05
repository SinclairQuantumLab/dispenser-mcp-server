"""Read-only human-dashboard view of explicitly synthetic observer files.

This module has no simulator, instrument, MCP, or decision-input imports.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, cast

STATE_FIELDS = {
    "output_enabled",
    "commanded_load_current_a",
    "delivered_current_a",
    "delivered_voltage_v",
    "heating_power_w",
    "thermal_state",
    "rb_remaining_fraction",
    "impurity_remaining_fraction",
    "rb_remaining_effective_units",
    "impurity_remaining_effective_units",
    "rb_emitted_effective_units",
    "impurity_emitted_effective_units",
    "rb_release_rate_effective_units_per_s",
    "impurity_release_rate_effective_units_per_s",
    "rb_chamber_effective_units",
    "impurity_chamber_effective_units",
    "rb_removed_effective_units",
    "impurity_removed_effective_units",
    "background_pressure_mbar",
    "rb_pressure_mbar",
    "impurity_pressure_mbar",
    "total_pressure_mbar",
}
PARAMETER_FIELDS = {
    "resistance_ohm",
    "initial_rb_effective_units",
    "initial_impurity_effective_units",
    "initial_rb_to_impurity_effective_ratio",
    "thermal_tau_s",
    "chamber_tau_s",
    "reference_power_w",
    "impurity_reference_rate_s",
    "impurity_alpha",
    "rb_reference_rate_s",
    "rb_alpha",
    "rb_pressure_gain_mbar_per_effective_unit",
    "impurity_pressure_gain_mbar_per_effective_unit",
}


def _object_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError(f"Oversized metadata file: {path.name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"Expected metadata object: {path.name}")
    return cast(dict[str, Any], value)


def _numbers(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Missing state or parameters object")
    data = cast(dict[str, Any], value)
    selected: dict[str, Any] = {}
    for key in fields & data.keys():
        number = data[key]
        if key == "output_enabled":
            if type(number) is not bool:
                raise ValueError("Invalid output_enabled")
        elif number is not None:
            if (
                type(number) not in (int, float)
                or not math.isfinite(number)
                or number < 0
            ):
                raise ValueError(f"Invalid numeric field: {key}")
            if key.endswith("_fraction") and number > 1:
                raise ValueError(f"Fraction outside 0..1: {key}")
        selected[key] = number
    return selected


class SimulationObserverReader:
    """One simulated session/run; never merge different runs into its plot."""

    def __init__(
        self,
        session_directory: Path,
        observer_file: Path | None = None,
        expected_run_id: str | None = None,
    ) -> None:
        self.directory = Path(session_directory).resolve()
        self.path = (
            Path(observer_file).resolve()
            if observer_file
            else self.directory / "observer.jsonl"
        )
        self.expected_run_id = expected_run_id
        self.offset = 0
        self.identity: tuple[int, int] | None = None
        self.rows: list[dict[str, Any]] = []
        self.generation = 0
        self.line_count = 0
        self.errors = 0
        self.last_error: str | None = None
        self.run_id: str | None = None
        self.parameters: dict[str, Any] = {}
        self._conflict: str | None = None
        self._total_rows = 0

    def _association(self, metadata: dict[str, Any]) -> tuple[str | None, str]:
        expected = self.expected_run_id
        link_path = self.directory / "observer-link.json"
        if link_path.exists():
            link = _object_file(link_path)
            if link.get("session_id") != metadata["session_id"]:
                raise ValueError("Observer linkage belongs to a different session")
            linked_run = link.get("run_id")
            if not isinstance(linked_run, str) or not linked_run:
                raise ValueError("Observer linkage has no run_id")
            if not isinstance(link.get("observer_file"), str):
                raise ValueError("Observer linkage has no file path")
            linked_path = Path(link["observer_file"])
            if not linked_path.is_absolute():
                linked_path = self.directory / linked_path
            if linked_path.resolve() != self.path:
                raise ValueError("Observer linkage names a different file")
            if expected is not None and expected != linked_run:
                raise ValueError("Operator run_id conflicts with session linkage")
            return linked_run, "session_link"
        if expected:
            return expected, "operator_run_id"
        return (
            None,
            "same_directory"
            if self.path.parent == self.directory
            else "operator_selected",
        )

    def snapshot(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "waiting",
            "message": None,
            "session_id": None,
            "run_id": None,
            "association": None,
            "source": str(self.path),
            "generation": self.generation,
            "errors": self.errors,
            "last_error": self.last_error,
            "model_revision": "two_inventory_v1",
            "units": {
                "inventory": "synthetic_effective_units",
                "fractions": "0..1",
                "thermal": "normalized_not_kelvin",
                "pressure": "mbar",
            },
            "uncalibrated": True,
            "human_only": True,
            "parameters": {},
            "rows": [],
            "runs": [],
            "dropped_rows": 0,
        }
        try:
            metadata = _object_file(self.directory / "metadata.json")
            if metadata.get("session_kind") != "simulated":
                return {
                    **payload,
                    "status": "unavailable",
                    "message": "Internal model state is unavailable for non-simulated sessions",
                }
            if (
                not isinstance(metadata.get("session_id"), str)
                or not metadata["session_id"]
            ):
                raise ValueError("Simulated session metadata lacks session_id")
            payload["session_id"] = metadata["session_id"]
            expected, association = self._association(metadata)
            payload["association"] = association
            stat = self.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity != self.identity or stat.st_size < self.offset:
                self.offset = self.line_count = self.errors = self._total_rows = 0
                self.rows.clear()
                self.run_id = self.last_error = self._conflict = None
                self.parameters = {}
                self.generation += 1
                self.identity = identity
            if (
                expected is not None
                and self.run_id is not None
                and expected != self.run_id
            ):
                return {
                    **payload,
                    "status": "mismatch",
                    "message": "Run linkage conflicts with the loaded observer run",
                }
            partial = False
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                for _ in range(5000):
                    raw = stream.readline(1024 * 1024 + 1)
                    if not raw:
                        break
                    if len(raw) > 1024 * 1024:
                        self._conflict = "Observer row exceeds 1 MiB"
                        break
                    if not raw.endswith(b"\n"):
                        partial = True
                        break
                    self.offset = stream.tell()
                    self.line_count += 1
                    if not raw.strip():
                        continue
                    try:
                        self._append(json.loads(raw), metadata["session_id"], expected)
                    except (ValueError, TypeError, KeyError) as error:
                        self.errors += 1
                        self.last_error = f"Line {self.line_count}: {error}"
                        self._conflict = self.last_error
                pending = stream.tell() < stat.st_size and not partial
            payload.update(
                generation=self.generation,
                errors=self.errors,
                last_error=self.last_error,
                run_id=self.run_id,
                dropped_rows=max(0, self._total_rows - len(self.rows)),
            )
            if self._conflict:
                return {**payload, "status": "mismatch", "message": self._conflict}
            if pending:
                return {
                    **payload,
                    "message": "Scanning remaining rows before confirming a single run",
                }
            if not self.rows:
                return {**payload, "message": "Waiting for a complete observer row"}
            rows = list(self.rows)
            message = "Waiting for a complete final row" if partial else None
            return {
                **payload,
                "status": "ready",
                "message": message,
                "parameters": self.parameters,
                "rows": rows,
                "runs": [{"run_id": self.run_id, "rows": rows}],
            }
        except FileNotFoundError:
            return {
                **payload,
                "message": "Waiting for session metadata or observer file",
            }
        except OSError as error:
            return {**payload, "status": "error", "message": str(error)}
        except (ValueError, TypeError, KeyError) as error:
            return {**payload, "status": "mismatch", "message": str(error)}

    def _append(self, value: Any, session_id: str, expected: str | None) -> None:
        if not isinstance(value, dict):
            raise ValueError("Expected observer object")
        row = cast(dict[str, Any], value)
        if (
            type(row.get("schema_version")) is not int
            or row["schema_version"] != 1
        ):
            raise ValueError("Expected observer schema_version 1")
        if (
            row.get("simulated") is not True
            or row.get("model_revision") != "two_inventory_v1"
        ):
            raise ValueError("Expected explicitly simulated two_inventory_v1 state")
        run = row.get("run_id")
        sequence, seconds = row.get("sequence"), row.get("virtual_time_s")
        if not isinstance(run, str) or not run:
            raise ValueError("Missing run_id")
        if (expected is not None and run != expected) or (
            self.run_id is not None and run != self.run_id
        ):
            raise ValueError("Observer contains a mismatched or multiple run_id")
        if "session_id" in row and row["session_id"] != session_id:
            raise ValueError("Observer row belongs to a different session")
        if type(sequence) is not int or sequence < 0:
            raise ValueError("Invalid sequence")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError("Invalid virtual_time_s")
        if self.rows and (
            sequence <= self.rows[-1]["sequence"]
            or seconds < self.rows[-1]["virtual_time_s"]
        ):
            raise ValueError(
                "Sequence must increase and virtual time must not decrease"
            )
        parameters = _numbers(row.get("parameters"), PARAMETER_FIELDS)
        state = _numbers(row.get("state"), STATE_FIELDS)
        if self.run_id and parameters != self.parameters:
            raise ValueError("Fixed unit parameters changed within the run")
        if row.get("kind") not in {"init", "advance", "call"}:
            raise ValueError("Unsupported observer row kind")
        if row.get("observed_at") is not None and not isinstance(
            row["observed_at"], str
        ):
            raise ValueError("Invalid observed_at")
        self.run_id, self.parameters = run, parameters
        self.rows.append(
            {
                "run_id": run,
                "sequence": sequence,
                "virtual_time_s": seconds,
                "observed_at": row.get("observed_at"),
                "kind": row.get("kind"),
                "state": state,
                "parameters": parameters,
            }
        )
        self._total_rows += 1
