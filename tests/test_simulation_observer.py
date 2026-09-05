"""File-only human observer association and incremental reading checks."""

import json
from pathlib import Path

import pytest

from dispenser_conditioning_mcp.simulation_observer import SimulationObserverReader


def session(tmp_path: Path, kind="simulated") -> Path:
    directory = tmp_path / "session"
    directory.mkdir()
    (directory / "metadata.json").write_text(
        json.dumps({"session_id": "session-a", "session_kind": kind})
    )
    return directory


def row(sequence=0, run_id="run-a"):
    return {
        "schema_version": 1,
        "simulated": True,
        "model_revision": "two_inventory_v1",
        "run_id": run_id,
        "sequence": sequence,
        "virtual_time_s": sequence * 15,
        "observed_at": "2040-01-01T00:00:00Z",
        "kind": "init" if sequence == 0 else "advance",
        "parameters": {
            "resistance_ohm": 0.15,
            "initial_rb_effective_units": 1.0,
            "initial_impurity_effective_units": 1.1,
            "private_seed": "never-return-this",
        },
        "state": {
            "rb_remaining_fraction": 1.0,
            "impurity_remaining_fraction": 1.0,
            "thermal_state": 0.0,
            "private_detail": "never-return-this",
        },
    }


def append(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value) + "\n")


def test_same_directory_incremental_partial_and_no_private_fields(tmp_path):
    directory = session(tmp_path)
    path = directory / "observer.jsonl"
    reader = SimulationObserverReader(directory)
    assert reader.snapshot()["status"] == "waiting"
    append(path, row())
    first = reader.snapshot()
    assert first["status"] == "ready"
    assert first["association"] == "same_directory"
    assert first["parameters"]["resistance_ohm"] == 0.15
    assert "never-return-this" not in json.dumps(first)
    encoded = json.dumps(row(1))
    with path.open("a") as stream:
        stream.write(encoded[:30])
    assert len(reader.snapshot()["rows"]) == 1
    with path.open("a") as stream:
        stream.write(encoded[30:] + "\n")
    assert [item["sequence"] for item in reader.snapshot()["rows"]] == [0, 1]
    assert len(reader.snapshot()["rows"]) == 2


def test_external_operator_selection_and_expected_run(tmp_path):
    directory = session(tmp_path)
    path = tmp_path / "external.jsonl"
    append(path, row())
    result = SimulationObserverReader(directory, path).snapshot()
    assert result["status"] == "ready" and result["association"] == "operator_selected"
    assert (
        SimulationObserverReader(directory, path, "wrong").snapshot()["status"]
        == "mismatch"
    )


def test_link_matches_session_file_and_run(tmp_path):
    directory = session(tmp_path)
    path = directory / "observer.jsonl"
    append(path, row())
    link = {"session_id": "session-a", "run_id": "run-a", "observer_file": str(path)}
    link_path = directory / "observer-link.json"
    link_path.write_text(json.dumps(link))
    reader = SimulationObserverReader(directory)
    assert reader.snapshot()["association"] == "session_link"
    link_path.write_text(json.dumps({**link, "run_id": "wrong"}))
    assert reader.snapshot()["status"] == "mismatch"
    link_path.write_text(json.dumps({**link, "session_id": "foreign"}))
    assert SimulationObserverReader(directory).snapshot()["status"] == "mismatch"


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "run-b"},
        {"session_id": "foreign"},
        {"sequence": 0},
        {"virtual_time_s": -1},
        {"simulated": False},
        {"state": {"thermal_state": float("nan")}},
    ],
)
def test_mismatched_or_invalid_rows_never_merge(tmp_path, change):
    directory = session(tmp_path)
    path = directory / "observer.jsonl"
    append(path, row())
    reader = SimulationObserverReader(directory)
    assert reader.snapshot()["status"] == "ready"
    append(path, {**row(1), **change})
    result = reader.snapshot()
    assert result["status"] == "mismatch"
    assert result["rows"] == [] and result["runs"] == []


def test_live_session_never_reads_observer(tmp_path):
    directory = session(tmp_path, "live")
    (directory / "observer.jsonl").write_text("invalid-secret-like-text")
    result = SimulationObserverReader(directory).snapshot()
    assert result["status"] == "unavailable" and result["rows"] == []
    assert result["errors"] == 0


def test_replacement_resets_generation_without_merging(tmp_path):
    directory = session(tmp_path)
    path = directory / "observer.jsonl"
    append(path, row())
    reader = SimulationObserverReader(directory)
    first = reader.snapshot()
    replacement = directory / "replacement.jsonl"
    append(replacement, row(run_id="run-b"))
    replacement.replace(path)
    result = reader.snapshot()
    assert result["generation"] == first["generation"] + 1
    assert result["run_id"] == "run-b" and len(result["rows"]) == 1
