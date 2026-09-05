from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette

from dispenser_conditioning_mcp.dashboard import dashboard_routes
from dispenser_conditioning_mcp.session_records import SessionRecorder


@pytest.mark.anyio
async def test_human_routes_do_not_expose_observer_for_live_session(tmp_path: Path):
    (tmp_path / "metadata.json").write_text(
        json.dumps({"session_id": "fixture", "session_kind": "live"})
    )
    # An operator-selected file must not override the source-mode boundary.
    observer = tmp_path / "observer.jsonl"
    observer.write_text('{"private_fixture": "not for live view"}\n')
    app = Starlette(routes=dashboard_routes(tmp_path, observer_file=observer))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        page = await client.get("/dashboard")
        assert page.status_code == 200
        assert "Selected record and linked details" in page.text
        response = await client.get(
            "/api/simulation-state", headers={"Origin": "http://localhost"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "unavailable"
        assert response.json()["rows"] == []
        assert "private_fixture" not in response.text
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_run_selection_is_per_request_and_confined(tmp_path: Path, monkeypatch):
    from dispenser_conditioning_mcp import run_directory

    runs = tmp_path / "runs"
    monkeypatch.setattr(run_directory, "RUNS_DIRECTORY", runs)
    current = SessionRecorder(
        tmp_path / "outside-current",
        source="scripted",
        session_kind="live",
        label="Current",
    )
    saved = SessionRecorder(
        runs / "saved", source="scripted", session_kind="simulated", label="Saved"
    )
    for recorder in (current, saved):
        recorder.append_event(
            "call_intent",
            {"tool": "read_vacuum_pressure", "arguments": {}},
            call_id="call",
        )
    (runs / "legacy").mkdir()
    app = Starlette(routes=dashboard_routes(current.directory))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        listing = (await client.get("/api/runs")).json()
        assert listing["live_view_available"] is True
        legacy = next(row for row in listing["runs"] if row["key"] == "legacy")
        assert legacy["available"] is False
        assert "metadata.json" in legacy["reason"]
        archive = (await client.get("/api/session?run=saved")).json()
        assert archive["metadata"]["session_id"] == saved.session_id
        assert archive["recording_view"] == "saved_recording"
        original = (await client.get("/api/session")).json()
        assert original["metadata"]["session_id"] == current.session_id
        assert original["recording_view"] == "process_session"
        assert archive["events"][0]["event_id"] != original["events"][0]["event_id"]
        for endpoint in ("/api/session", "/api/simulation-state"):
            for name in ("../outside-current", "legacy", "missing", str(tmp_path)):
                assert (
                    await client.get(endpoint, params={"run": name})
                ).status_code == 400
        observer = (await client.get("/api/simulation-state?run=saved")).json()
        assert observer["status"] == "waiting"
        assert (await client.get("/api/simulation-state")).json()[
            "status"
        ] == "unavailable"
    preview = Starlette(routes=dashboard_routes(current.directory, replay=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=preview), base_url="http://localhost"
    ) as client:
        assert (await client.get("/api/runs")).json()["live_view_available"] is False
        assert (await client.get("/api/session")).json()[
            "recording_view"
        ] == "saved_recording"
