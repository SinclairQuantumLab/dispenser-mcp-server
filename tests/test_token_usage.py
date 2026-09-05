import csv
import json
from pathlib import Path

import pytest
from mcp import Client
from pydantic import ValidationError
from test_server_recording import context, setup

from dispenser_conditioning_mcp.recording_service import ActionContext, TokenUsage
from dispenser_simulator.contract import action_context_schema


@pytest.mark.parametrize(
    "field", ["total_tokens", "input_tokens", "output_tokens", "cached_input_tokens"]
)
@pytest.mark.parametrize("bad", [-1, True, 1.5, "10"])
def test_usage_counts_are_strict_nonnegative_integers(field, bad):
    with pytest.raises(ValidationError):
        TokenUsage.model_validate({"usage_id": "test", "total_tokens": 1, field: bad})


def test_optional_partial_usage_and_simulation_schema():
    partial = TokenUsage.model_validate({"usage_id": "partial", "total_tokens": 0})
    assert partial.input_tokens is None
    assert partial.cached_input_tokens is None
    schema = action_context_schema()
    assert "token_usage" not in schema["required"]
    assert schema["properties"]["token_usage"]["required"] == [
        "usage_id",
        "total_tokens",
    ]
    assert ActionContext.model_fields["token_usage"].default is None


@pytest.mark.anyio
async def test_native_mcp_usage_and_csv_roundtrip(tmp_path: Path):
    server, service, controller = setup(tmp_path)
    usage = {
        "usage_id": "batch-1",
        "total_tokens": 100,
        "input_tokens": 80,
        "output_tokens": 20,
        "cached_input_tokens": 30,
        "model": 'fixture,"quoted"',
    }
    supplied = {**context(service), "token_usage": usage}
    async with Client(server) as client:
        first = await client.call_tool(
            "prepare_dispenser_power", {"action_context": supplied}
        )
        assert not first.is_error
        second = await client.call_tool(
            "record_conditioning_decision", {"action_context": supplied}
        )
        assert not second.is_error
        absent = await client.call_tool(
            "record_conditioning_decision",
            {"action_context": {**context(service), "token_usage": None}},
        )
        assert not absent.is_error
    assert controller.calls == [("prepare", None)]
    with (service.directory / "decisions.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert [row["token_usage_id"] for row in rows] == ["batch-1", "batch-1", ""]
    assert rows[0]["total_tokens"] == "100"
    assert rows[0]["cached_input_tokens"] == "30"
    assert rows[0]["token_model"] == usage["model"]
    assert rows[-1]["total_tokens"] == ""
    events = [
        json.loads(line)
        for line in (service.directory / "events.jsonl").read_text().splitlines()
    ]
    decision = next(event for event in events if event["kind"] == "decision")
    assert decision["payload"]["action_context"]["token_usage"] == usage


@pytest.mark.anyio
async def test_simulation_native_declaration_accepts_partial_usage(tmp_path: Path):
    from dispenser_conditioning_mcp.recording_service import RecordingService
    from dispenser_conditioning_mcp.session_records import SessionRecorder
    from dispenser_conditioning_mcp.simulation_app import SimulationMCPServer
    from dispenser_simulator.model import (
        HiddenSimulatorConfig,
        SimulatedDispenser,
        ToolRouter,
    )
    from dispenser_simulator.recording import RecordingAdapter

    simulator = SimulatedDispenser(
        HiddenSimulatorConfig(seed="usage-fixture", scenario="nominal_recovery"),
        monotonic=lambda: 0,
    )
    service = RecordingService(
        SessionRecorder(
            tmp_path / "simulation",
            source="scripted",
            session_kind="simulated",
            label="Token usage fixture",
        )
    )
    server = SimulationMCPServer(RecordingAdapter(ToolRouter(simulator), service))
    async with Client(server) as client:
        result = await client.call_tool(
            "record_conditioning_decision",
            {
                "action_context": {
                    **context(service),
                    "token_usage": {"usage_id": "partial-sim", "total_tokens": 25},
                }
            },
        )
        assert not result.is_error
    assert simulator.state.virtual_time_s == 0
    assert not simulator.state.ch1_output_on
