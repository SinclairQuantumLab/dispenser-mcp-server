import json
from pathlib import Path

import pytest
from mcp import Client

from dispenser_conditioning_mcp.recording_service import RecordingService
from dispenser_conditioning_mcp.session_records import SessionRecorder
from dispenser_conditioning_mcp.simulation_app import SimulationMCPServer
from dispenser_simulator.model import (
    HiddenSimulatorConfig,
    SimulatedDispenser,
    ToolRouter,
)
from dispenser_simulator.recording import RecordingAdapter


@pytest.mark.anyio
async def test_public_timing_validation_declaration_and_error_recording(tmp_path: Path):
    now = [100.0]
    simulator = SimulatedDispenser(
        HiddenSimulatorConfig(seed="timing-fixture", scenario="nominal_recovery"),
        monotonic=lambda: now[0],
    )
    service = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="simulated",
            label="Timing fixture",
        )
    )
    server = SimulationMCPServer(RecordingAdapter(ToolRouter(simulator), service))
    async with Client(server) as client:
        for value in (-1, True, "10"):
            rejected = await client.call_tool(
                "read_vacuum_pressure", {"elapsed_s": value}
            )
            assert rejected.is_error
            assert (
                rejected.meta["dispenser_conditioning"]["execution"] == "not_executed"
            )
        assert simulator.state.virtual_time_s == 0
        first = await client.call_tool("read_vacuum_pressure", {"elapsed_s": 60})
        assert first.structured_content["timing"] == {
            "requested_elapsed_s": 60,
            "wall_elapsed_s": 0,
            "advanced_s": 60,
            "virtual_time_s": 60,
        }
        context = {
            "session_id": service.session_id,
            "decision_at": "2026-09-05T12:00:00Z",
            "action": "Fixture decision",
            "background": "Local timing test",
            "rationale_summary": "Exercise timing without hardware",
            "observation_ids": [first.meta["dispenser_conditioning"]["observation_id"]],
            "confidence": {"claim": "This is a fixture", "value": 1},
        }
        now[0] = 120
        declaration = await client.call_tool(
            "record_conditioning_decision", {"action_context": context}
        )
        assert not declaration.is_error
        assert simulator.state.virtual_time_s == 60
        now[0] = 140
        failed = await client.call_tool(
            "set_dispenser_current",
            {
                "action_context": context,
                "target_current_a": 0.2,
                "expected_current_a": 0,
                "elapsed_s": 5,
            },
        )
        assert failed.is_error
        assert failed.meta["simulation_timing"]["advanced_s"] == 40
        assert simulator.state.virtual_time_s == 100
        shutdown = await client.call_tool("shutdown_dispenser_power", {})
        assert not shutdown.is_error
        assert shutdown.structured_content["output_enabled"] is False
    events = [
        json.loads(line)
        for line in (service.directory / "events.jsonl").read_text().splitlines()
    ]
    assert any(
        event["kind"] == "call_result"
        and (event["payload"]["result"].get("_meta") or {})
        .get("simulation_timing", {})
        .get("advanced_s")
        == 40
        for event in events
    )
