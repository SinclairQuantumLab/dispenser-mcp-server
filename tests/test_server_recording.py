from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.types import CallToolResult
from test_protocol import FakePowerController, FakePressureSource

from dispenser_conditioning_mcp.power_domain import PowerControlError
from dispenser_conditioning_mcp.recording_service import RecordingService
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.session_records import SessionRecorder
from dispenser_conditioning_mcp.transport import (
    McpTransportConfiguration,
    create_http_app,
)


def metadata(result: CallToolResult) -> dict[str, Any]:
    assert result.meta is not None
    return result.meta["dispenser_conditioning"]


def context(
    service: RecordingService, observation_id: str | None = None
) -> dict[str, Any]:
    return {
        "session_id": service.session_id,
        "decision_at": "2026-09-05T12:00:00Z",
        "action": "Protocol fixture action",
        "background": "No hardware involved",
        "rationale_summary": "Exercise the server-owned recording boundary",
        "observation_ids": [observation_id] if observation_id else [],
        "confidence": {"claim": "The fixture is synthetic", "value": 1.0},
    }


def setup(tmp_path: Path, power: FakePowerController | None = None):
    recorder = SessionRecorder(
        tmp_path / "session",
        source="scripted",
        session_kind="format_fixture",
        label="MCP recording fixture",
    )
    service = RecordingService(recorder)
    controller = power or FakePowerController()
    server = create_server(FakePressureSource(), controller, recording=service)
    return server, service, controller


@pytest.mark.anyio
async def test_direct_client_records_context_calls_and_nonactuating_completion(
    tmp_path: Path,
) -> None:
    server, service, controller = setup(tmp_path)
    async with Client(server) as client:
        reading = await client.call_tool("read_vacuum_pressure", {})
        ids = metadata(reading)
        ctx = context(service, ids["observation_id"])
        missing = await client.call_tool("prepare_dispenser_power", {})
        invalid = await client.call_tool(
            "prepare_dispenser_power", {"action_context": {**ctx, "decision_at": 42}}
        )
        unknown = await client.call_tool(
            "prepare_dispenser_power",
            {"action_context": {**ctx, "observation_ids": ["missing"]}},
        )
        assert all(
            metadata(result)["execution"] == "not_executed"
            for result in (missing, invalid, unknown)
        )
        assert controller.calls == []
        prepared = await client.call_tool(
            "prepare_dispenser_power", {"action_context": ctx}
        )
        completed = await client.call_tool(
            "record_conditioning_decision",
            {
                "action_context": ctx,
                "completion": {
                    "outcome": "incomplete",
                    "dispenser_response": "unknown",
                },
            },
        )
    assert prepared.is_error is False
    assert metadata(prepared)["observation_id"] is not None
    assert completed.is_error is False
    assert completed.structured_content is not None
    assert completed.structured_content["hardware_action_performed"] is False
    assert controller.calls == [("prepare", None)]
    events = [
        json.loads(line)
        for line in (service.directory / "events.jsonl").read_text().splitlines()
    ]
    assert all("schema_version" not in event for event in events)
    assert "schema_version" not in json.loads(
        (service.directory / "metadata.json").read_text()
    )
    assert any(
        event["kind"] == "decision" and event["payload"].get("completion")
        for event in events
    )
    assert len({event["call_id"] for event in events}) == 6
    assert (service.directory / "observations.csv").is_file()


@pytest.mark.anyio
async def test_shutdown_precedes_logging_and_log_failure_preserves_hardware_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, service, controller = setup(tmp_path)

    def fail_append(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert controller.calls == [("shutdown", None)]
        raise OSError("fixture logging unavailable")

    monkeypatch.setattr(service, "_append", fail_append)
    async with Client(server) as client:
        result = await client.call_tool("shutdown_dispenser_power", {})
    assert result.is_error is False
    assert metadata(result)["execution"] == "completed"
    assert metadata(result)["recording_status"] == "degraded"


@pytest.mark.anyio
async def test_sync_controller_failure_is_never_labeled_not_executed(
    tmp_path: Path,
) -> None:
    controller = FakePowerController(
        error=PowerControlError("Fixture: output outcome unknown")
    )
    server, service, _ = setup(tmp_path, controller)
    async with Client(server) as client:
        result = await client.call_tool(
            "prepare_dispenser_power", {"action_context": context(service)}
        )
    assert result.is_error is True
    assert controller.calls == [("prepare", None)]
    assert metadata(result)["execution"] == "failed_or_unknown"


@pytest.mark.anyio
async def test_shared_http_app_serves_browser_routes_and_records_without_wrapper(
    tmp_path: Path,
) -> None:
    server, service, _ = setup(tmp_path)
    app = create_http_app(server, McpTransportConfiguration())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as http,
    ):
        browser = await http.get(
            "/dashboard", headers={"Origin": "http://127.0.0.1:8000"}
        )
        assert browser.status_code == 200
        assert "MCP records the requests and readings" in browser.text
        blocked = await http.post(
            "/mcp", headers={"Origin": "http://127.0.0.1:8000"}, json={}
        )
        assert blocked.status_code == 403
        # Use the current protocol directly over HTTP; no client-side recorder.
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
        }
        initialized = await http.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "direct-http-fixture", "version": "1"},
                },
            },
        )
        assert initialized.status_code == 200
        if "mcp-session-id" in initialized.headers:
            headers["Mcp-Session-Id"] = initialized.headers["mcp-session-id"]
        response = await http.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "read_vacuum_pressure", "arguments": {}},
            },
        )
        assert response.status_code == 200, response.text
        assert "dispenser_conditioning" in response.text
        data = (await http.get("/api/session")).json()
        assert len([row for row in data["events"] if row.get("observation_kind")]) == 1
        assert data["metadata"]["session_id"] == service.session_id
