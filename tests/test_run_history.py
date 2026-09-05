import json
from pathlib import Path

import httpx
import pytest
from mcp import Client
from starlette.applications import Starlette
from test_server_recording import context, setup
from test_simulation_observer import row

from dispenser_conditioning_mcp import run_directory
from dispenser_conditioning_mcp.dashboard import dashboard_routes
from dispenser_conditioning_mcp.run_history import RunCatalog, RunHistory
from dispenser_conditioning_mcp.session_records import SessionRecorder


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["complete", "incomplete", "aborted", "unknown"])
async def test_recorded_completion_unlocks_current_hindsight_without_relocking(
    tmp_path, outcome
):
    server, service, _ = setup(tmp_path)
    metadata_file = service.directory / "metadata.json"
    metadata = json.loads(metadata_file.read_text())
    metadata["session_kind"] = "simulated"
    metadata_file.write_text(json.dumps(metadata))
    (service.directory / "observer.jsonl").write_text(json.dumps(row()) + "\n")
    async with Client(server) as client:
        assert (await client.call_tool("read_saved_simulation_state", {})).is_error
        result = await client.call_tool(
            "record_conditioning_decision",
            {
                "action_context": context(service),
                "completion": {"outcome": outcome, "dispenser_response": "unknown"},
            },
        )
        assert not result.is_error and service.completion_recorded
        history = await client.call_tool("read_saved_simulation_state", {})
        assert (
            history.structured_content["review_mode"]
            == "completed_current_simulation_hindsight_not_live_observation"
        )
        assert len(history.structured_content["rows"]) == 1
        await client.call_tool(
            "record_conditioning_decision", {"action_context": context(service)}
        )
        assert service.completion_recorded
        assert not (await client.call_tool("read_saved_simulation_state", {})).is_error


@pytest.mark.anyio
async def test_missing_invalid_or_degraded_completion_does_not_unlock(
    tmp_path, monkeypatch
):
    server, service, _ = setup(tmp_path)
    async with Client(server) as client:
        await client.call_tool(
            "record_conditioning_decision", {"action_context": context(service)}
        )
        assert not service.completion_recorded
        invalid = await client.call_tool(
            "record_conditioning_decision",
            {
                "action_context": context(service),
                "completion": {"outcome": "invalid"},
            },
        )
        assert invalid.is_error and not service.completion_recorded
        original = service._append

        def fail_final(kind, payload, call_id, decision_id):
            if kind == "call_result":
                raise OSError("Fixture final recording failure")
            return original(kind, payload, call_id, decision_id)

        monkeypatch.setattr(service, "_append", fail_final)
        result = await client.call_tool(
            "record_conditioning_decision",
            {
                "action_context": context(service),
                "completion": {"outcome": "aborted", "dispenser_response": "unknown"},
            },
        )
        assert result.meta["dispenser_conditioning"]["recording_status"] == "degraded"
        assert not service.completion_recorded
        assert (await client.call_tool("read_saved_simulation_state", {})).is_error


def fixture(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(run_directory, "RUNS_DIRECTORY", runs)
    current = SessionRecorder(
        runs / "current",
        source="scripted",
        session_kind="simulated",
        label="History fixture",
    )
    saved = SessionRecorder(
        runs / "saved",
        source="scripted",
        session_kind="simulated",
        label="Saved fixture",
    )
    for recorder in (current, saved):
        recorder.append_event(
            "call_intent",
            {"tool": "read_vacuum_pressure", "arguments": {}},
            call_id="public-call",
        )
        (recorder.directory / "observer.jsonl").write_text(json.dumps(row()) + "\n")
    return current, saved


@pytest.mark.anyio
async def test_history_paging_raw_and_saved_only_hindsight(tmp_path, monkeypatch):
    current, saved = fixture(tmp_path, monkeypatch)
    history = RunHistory(current.directory)
    for _ in range(202):
        current.append_event(
            "call_intent", {"tool": "read_vacuum_pressure", "arguments": {}}
        )
    original = (current.directory / "events.jsonl").read_bytes()
    first = (await history.call("read_conditioning_run", {})).structured_content
    assert first and len(first["events"]) == 200 and first["has_more"]
    second = (
        await history.call(
            "read_conditioning_run",
            {"after": first["cursor"], "generation": first["generation"]},
        )
    ).structured_content
    assert second and len(second["events"]) == 3 and not second["has_more"]
    assert not set(e["event_id"] for e in first["events"]) & set(
        e["event_id"] for e in second["events"]
    )
    assert "source" not in first and str(tmp_path) not in json.dumps(first)
    event_id = first["events"][0]["event_id"]
    raw = (
        await history.call("read_conditioning_run", {"event_id": event_id})
    ).structured_content
    assert raw and raw["event"]["payload"]["tool"] == "read_vacuum_pressure"
    for key in ("", "current"):
        denied = await history.call("read_saved_simulation_state", {"run_key": key})
        assert denied.is_error and "Current-process" in denied.content[0].text
    hindsight = (
        await history.call("read_saved_simulation_state", {"run_key": "saved"})
    ).structured_content
    assert hindsight and hindsight["status"] == "ready" and len(hindsight["rows"]) == 1
    assert "hindsight" in hindsight["review_mode"]
    assert "never-return-this" not in json.dumps(hindsight) and str(
        tmp_path
    ) not in json.dumps(hindsight)
    assert (current.directory / "events.jsonl").read_bytes() == original
    listing = (
        await history.call("list_conditioning_runs", {"limit": 1})
    ).structured_content
    assert listing and listing["has_more"] and listing["cursor"] == 1
    next_page = (
        await history.call("list_conditioning_runs", {"limit": 1, "after": 1})
    ).structured_content
    assert next_page and len(next_page["runs"]) == 1 and not next_page["has_more"]
    (saved.directory / "run-management.json").write_text("broken JSON")
    assert len(history.catalog.list()) == 2
    assert not next(item for item in history.catalog.list() if item["key"] == "saved")[
        "available"
    ]


@pytest.mark.anyio
async def test_native_history_bypasses_instrument_and_experiment_recording(tmp_path):
    server, service, controller = setup(tmp_path)
    del controller
    before = (
        (service.directory / "events.jsonl").read_bytes()
        if (service.directory / "events.jsonl").exists()
        else b""
    )
    async with Client(server) as client:
        assert len((await client.list_tools()).tools) == 11
        assert not (await client.call_tool("read_conditioning_run", {})).is_error
        assert (await client.call_tool("read_saved_simulation_state", {})).is_error
    after = (
        (service.directory / "events.jsonl").read_bytes()
        if (service.directory / "events.jsonl").exists()
        else b""
    )
    assert before == after


@pytest.mark.anyio
async def test_human_management_post_auth_archive_delete_and_cache(
    tmp_path, monkeypatch
):
    current, saved = fixture(tmp_path, monkeypatch)
    original = (saved.directory / "events.jsonl").read_bytes()
    app = Starlette(routes=dashboard_routes(current.directory))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/runs/archive")).status_code == 405
        assert (
            await client.post(
                "/api/runs/archive",
                json={"run": "saved"},
                headers={"Origin": "http://elsewhere"},
            )
        ).status_code == 403
        assert (
            await client.post("/api/runs/archive", json={"run": ""})
        ).status_code == 400
        renamed = await client.post(
            "/api/runs/rename", json={"run": "saved", "display_name": "Reviewed sample"}
        )
        assert renamed.json()["display_name"] == "Reviewed sample"
        assert (await client.get("/api/session?run=saved")).json()["metadata"][
            "label"
        ] == "Reviewed sample"
        assert (saved.directory / "events.jsonl").read_bytes() == original
        assert (
            await client.post(
                "/api/runs/delete", json={"run": "saved", "confirmation": "saved"}
            )
        ).status_code == 400
        assert (
            await client.post("/api/runs/archive", json={"run": "saved"})
        ).status_code == 200
        assert [
            item["key"]
            for item in (await client.get("/api/runs?archived=true")).json()["runs"]
        ] == ["saved"]
        assert (
            await client.post("/api/runs/restore", json={"run": "saved"})
        ).status_code == 200
        await client.post("/api/runs/archive", json={"run": "saved"})
        assert (
            await client.post(
                "/api/runs/delete", json={"run": "saved", "confirmation": "wrong"}
            )
        ).status_code == 400
        assert (
            await client.post(
                "/api/runs/delete", json={"run": "saved", "confirmation": "saved"}
            )
        ).json()["deleted"]
        assert not saved.directory.exists() and current.directory.exists()
        assert (await client.get("/api/session?run=saved")).status_code == 400
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.50", 123)),
        base_url="http://localhost",
    ) as remote:
        assert (
            await remote.post(
                "/api/runs/rename", json={"run": "", "display_name": "Denied"}
            )
        ).status_code == 401


def test_delete_refuses_nested_link(tmp_path, monkeypatch):
    current, saved = fixture(tmp_path, monkeypatch)
    catalog = RunCatalog(current.directory)
    catalog.manage("saved", "archive")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = saved.directory / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        # Windows may disallow symlink creation; exercise the same junction guard.
        link.mkdir()
        original = Path.is_junction
        monkeypatch.setattr(
            Path, "is_junction", lambda path: path == link or original(path)
        )
    with pytest.raises(ValueError, match="link"):
        catalog.manage("saved", "delete", confirmation="saved")
    assert outside.exists() and saved.directory.exists()
