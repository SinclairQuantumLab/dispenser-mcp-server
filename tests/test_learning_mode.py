from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent
from test_protocol import (  # pyright: ignore[reportPrivateUsage]
    FakePowerController,
    FakePressureSource,
    _power_state,  # pyright: ignore[reportPrivateUsage]
    action_context,
)

from dispenser_conditioning_mcp.config import LearningModeConfiguration
from dispenser_conditioning_mcp.dashboard import dashboard_record
from dispenser_conditioning_mcp.learning_mode import (
    APPEND_RUN_REVIEW_TOOL,
    READ_RUN_NOTES_TOOL,
    READ_RUN_REVIEWS_TOOL,
    HumanManualOperation,
    LearningModeService,
    RunReviewRequest,
    SerializedHardwareBackend,
    SimulationLearningBackend,
)
from dispenser_conditioning_mcp.power_domain import (
    DispenserPowerState,
    PowerAcceptanceContext,
    PowerActionResult,
)
from dispenser_conditioning_mcp.recording_service import RecordingService
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.session_records import SessionRecorder
from dispenser_conditioning_mcp.simulation_app import SimulationMCPServer
from dispenser_conditioning_mcp.transport import (
    McpTransportConfiguration,
    create_http_app,
)
from dispenser_simulator.model import HiddenSimulatorConfig
from dispenser_simulator.recording import RecordingAdapter
from dispenser_simulator.server import build_runtime


def _result(data: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data))],
        structured_content=data,
    )


class SequenceBackend:
    acceptance_context = "production_dispenser"
    control_enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.power_reads = 0

    async def execute(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "read_vacuum_pressure":
            return _result(
                {
                    "observed_at": "2026-10-26T12:00:00Z",
                    "pressure_mbar": 1e-7,
                    "pressure_torr": 7.5e-8,
                    "source": "fixture.total_pressure",
                    "p1_drive_serial_number": "P1-FIXTURE",
                    "is_total_gauge_pressure": True,
                    "is_rubidium_partial_pressure": False,
                    "verifies_dispenser_activation": False,
                }
            )
        state = _power_state().model_dump(mode="json")
        if name == "read_dispenser_power_state":
            self.power_reads += 1
            if self.power_reads == 1:
                state["output_enabled"] = False
                state["native_current_setpoint_a"] = 0.0
                state["commanded_load_current_limit_a"] = 0.0
            return _result(state)
        return _result(
            {
                "action": name,
                "wrote_hardware": name != "set_dispenser_current",
                "state": state,
            }
        )


def _service(tmp_path: Path, backend: Any) -> LearningModeService:
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="format_fixture",
            label="Learning fixture",
        )
    )
    return LearningModeService(
        LearningModeConfiguration(enabled=True, sample_interval_s=3600.0),
        recording,
        backend,
        acceptance_context="production_dispenser",
        control_enabled=True,
    )


@pytest.mark.anyio
async def test_learning_mode_denies_all_agent_instrument_mutations_before_dispatch(
    tmp_path: Path,
) -> None:
    pressure = FakePressureSource()
    controller = FakePowerController()
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="format_fixture",
            label="Learning fixture",
        )
    )
    backend = SerializedHardwareBackend(pressure, controller, control_enabled=True)
    learning = LearningModeService(
        LearningModeConfiguration(enabled=True),
        recording,
        backend,
        acceptance_context="production_dispenser",
        control_enabled=True,
    )
    server = create_server(backend, backend, recording=recording, learning=learning)

    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        for name in (
            "prepare_dispenser_power",
            "enable_dispenser_output",
            "set_dispenser_current",
            "shutdown_dispenser_power",
        ):
            result = await client.call_tool(name, {})
            assert result.is_error is True
            assert isinstance(result.content[0], TextContent)
            assert "learning mode denies" in result.content[0].text
        status = await client.call_tool("read_learning_mode_status", {})

    assert controller.calls == []
    assert pressure.calls == 0
    assert {
        "read_learning_mode_status",
        APPEND_RUN_REVIEW_TOOL,
        READ_RUN_REVIEWS_TOOL,
        READ_RUN_NOTES_TOOL,
    }.issubset(names)
    assert status.structured_content is not None
    assert (
        status.structured_content["permissions"]["agent_instrument_mutations"] is False
    )


@pytest.mark.anyio
async def test_periodic_samples_use_projection_and_detect_unknown_external_change(
    tmp_path: Path,
) -> None:
    backend = SequenceBackend()
    service = _service(tmp_path, backend)

    await service.sample_once()
    await service.sample_once()

    events = [
        json.loads(line)
        for line in (service.recording.directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    change = next(row for row in events if row["kind"] == "external_change_detected")
    assert change["payload"]["source"] == "unknown"
    assert change["actor"] == "learning_sampler"
    assert change["payload"]["intent_known"] is False
    assert change["payload"]["exact_change_time_known"] is False
    assert "output_enabled" in change["payload"]["changed_fields"]
    public_change = dashboard_record(change)
    assert public_change["actor"] == "learning_sampler"
    assert public_change["change_source"] == "unknown"
    assert public_change["intent_known"] is False
    assert public_change["exact_change_time_known"] is False
    assert public_change["observation_interval_started_at"]
    assert public_change["detected_at"]
    assert public_change["changed_fields"]
    assert public_change["previous_values"]
    assert public_change["detected_values"]
    rows = (service.recording.directory / "observations.csv").read_text(
        encoding="utf-8"
    )
    assert rows.count("\n") == 5
    assert all(arguments == {} for _, arguments in backend.calls)
    power_result = next(
        row
        for row in events
        if row["kind"] == "call_result"
        and row["payload"].get("tool") == "read_dispenser_power_state"
    )
    projected = dashboard_record(power_result)
    assert projected["observation_kind"] == "power"
    assert projected["actor"] == "learning_sampler"


@pytest.mark.anyio
async def test_successful_agent_mutation_updates_sampler_baseline_without_hiding_drift(
    tmp_path: Path,
) -> None:
    class StatefulController(FakePowerController):
        def __init__(self) -> None:
            super().__init__()
            self.current_a = 0.0

        def _state(self) -> DispenserPowerState:
            return _power_state().model_copy(
                update={
                    "native_current_setpoint_a": self.current_a / 2,
                    "commanded_load_current_limit_a": self.current_a,
                }
            )

        def read_state(self) -> DispenserPowerState:
            self.calls.append(("read", None))
            return self._state()

        def set_current(
            self, *, target_current_a: float, expected_current_a: float
        ) -> PowerActionResult:
            assert expected_current_a == self.current_a
            self.current_a = target_current_a
            self.calls.append(("set", (target_current_a, expected_current_a)))
            return PowerActionResult(
                action="set_dispenser_current",
                wrote_hardware=True,
                state=self._state(),
            )

    controller = StatefulController()
    pressure = FakePressureSource()
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="agent",
            session_kind="format_fixture",
            label="Agent baseline fixture",
        )
    )
    backend = SerializedHardwareBackend(pressure, controller, control_enabled=True)
    learning = LearningModeService(
        LearningModeConfiguration(
            enabled=True,
            human_control_only=False,
            sample_interval_s=3600.0,
        ),
        recording,
        backend,
        acceptance_context="production_dispenser",
        control_enabled=True,
    )
    server = create_server(backend, backend, recording=recording, learning=learning)

    await learning.sample_once()
    result = await server.call_tool(
        "set_dispenser_current",
        {
            "target_current_a": 0.2,
            "expected_current_a": 0.0,
            "action_context": action_context(server),
        },
    )
    assert result.is_error is False
    await learning.sample_once()

    events = [
        json.loads(line)
        for line in (recording.directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert not any(event["kind"] == "external_change_detected" for event in events)

    controller.current_a = 0.4
    await learning.sample_once()
    events = [
        json.loads(line)
        for line in (recording.directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    changes = [event for event in events if event["kind"] == "external_change_detected"]
    assert len(changes) == 1
    assert "commanded_load_current_limit_a" in changes[0]["payload"]["changed_fields"]


@pytest.mark.anyio
async def test_lifespan_sampler_never_actuates_and_stops_without_cleanup_write(
    tmp_path: Path,
) -> None:
    backend = SequenceBackend()
    service = _service(tmp_path, backend)
    server = create_server(
        FakePressureSource(),
        FakePowerController(),
        recording=service.recording,
        learning=service,
    )
    app = create_http_app(server, McpTransportConfiguration())

    async with app.router.lifespan_context(app):
        for _ in range(100):
            if service.last_sample_at is not None:
                break
            await __import__("asyncio").sleep(0.001)

    assert service.running is False
    assert [name for name, _ in backend.calls] == [
        "read_vacuum_pressure",
        "read_dispenser_power_state",
    ]


@pytest.mark.anyio
async def test_review_is_idempotent_and_list_omits_full_detail(tmp_path: Path) -> None:
    service = _service(tmp_path, SequenceBackend())
    await service.sample_once()
    event_ids = [
        json.loads(line)["event_id"]
        for line in (service.recording.directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    payload = {
        "run_key": "",
        "review_id": "review-001",
        "report_summary": "The bounded report summary.",
        "change_summary": "One prospective wording change.",
        "source_ranges": [
            {"first_event_id": event_ids[0], "last_event_id": event_ids[-1]}
        ],
        "guidance_revision_refs": ["r000001:item"],
        "report_markdown": "# Full review\n\nDetailed analysis.",
        "guidance_changes": [
            {
                "revision_ref": "r000001:item",
                "before": "null",
                "after": '{"text":"prospective"}',
                "reason": "Retain a bounded learned qualification.",
                "scope": "future runs",
                "source_ranges": [
                    {
                        "first_event_id": event_ids[0],
                        "last_event_id": event_ids[-1],
                    }
                ],
            }
        ],
    }

    first = await service.call_agent_tool(APPEND_RUN_REVIEW_TOOL, payload)
    replay = await service.call_agent_tool(APPEND_RUN_REVIEW_TOOL, payload)
    listing = await service.call_agent_tool(
        READ_RUN_REVIEWS_TOOL, {"run_key": "", "after": 0, "limit": 50}
    )
    detail = await service.call_agent_tool(
        READ_RUN_REVIEWS_TOOL,
        {"run_key": "", "after": 0, "limit": 50, "review_id": "review-001"},
    )

    assert first.structured_content is not None
    assert first.structured_content["idempotent_replay"] is False
    assert replay.structured_content is not None
    assert replay.structured_content["idempotent_replay"] is True
    assert listing.structured_content is not None
    summary = listing.structured_content["items"][0]
    assert summary["detail_available"] is True
    assert "report_markdown" not in summary
    assert "guidance_changes" not in summary
    assert detail.structured_content is not None
    assert detail.structured_content["review"]["report_markdown"].startswith("# Full")
    assert (
        len(
            (service.recording.directory / "learning-reviews.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        == 1
    )

    changed = {**payload, "change_summary": "Different content"}
    conflict = await service.call_agent_tool(APPEND_RUN_REVIEW_TOOL, changed)
    assert conflict.is_error is True


@pytest.mark.anyio
async def test_human_routes_require_cookie_and_reuse_controller_contract(
    tmp_path: Path,
) -> None:
    pressure = FakePressureSource()
    controller = FakePowerController()
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="format_fixture",
            label="Learning fixture",
        )
    )
    backend = SerializedHardwareBackend(pressure, controller, control_enabled=True)
    learning = LearningModeService(
        LearningModeConfiguration(enabled=True, sample_interval_s=3600.0),
        recording,
        backend,
        acceptance_context="production_dispenser",
        control_enabled=True,
    )
    server = create_server(backend, backend, recording=recording, learning=learning)
    app = create_http_app(server, McpTransportConfiguration())
    access = app.state.dashboard_access
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client,
    ):
        denied = await client.post(
            "/api/learning/manual-operation",
            json={
                "run": "",
                "operation": "prepare",
                "action_context": {"action": "Prepare", "reason_summary": "Human"},
            },
        )
        assert denied.status_code == 401
        login = await client.post(
            "/dashboard/login",
            data={"code": access.token},
            headers={"Accept": "application/json", "Origin": "https://example.test"},
        )
        assert login.status_code == 200
        prepared = await client.post(
            "/api/learning/manual-operation",
            json={
                "run": "",
                "operation": "prepare",
                "action_context": {
                    "action": "Prepare from dashboard",
                    "reason_summary": "Operator selected the bounded preparation.",
                },
            },
            headers={"Origin": "https://example.test"},
        )
        note = await client.post(
            "/api/learning/note", json={"run": "", "note": "Operator note"}
        )
        snapshot = await client.get("/api/learning")

    assert prepared.status_code == 200
    assert prepared.json()["ok"] is True
    assert note.status_code == 200
    assert snapshot.json()["operator_authorized"] is True
    assert snapshot.json()["human_notes"]["items"][0]["note"] == "Operator note"
    assert ("prepare", None) in controller.calls
    events = [
        json.loads(line)
        for line in (recording.directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    manual_events = [
        event
        for event in events
        if event["payload"].get("tool") == "prepare_dispenser_power"
    ]
    assert manual_events
    assert {event["actor"] for event in manual_events} == {"human"}
    decision = next(event for event in events if event["kind"] == "decision")
    assert dashboard_record(decision)["actor"] == "human"

    notes = await learning.call_agent_tool(
        READ_RUN_NOTES_TOOL, {"run_key": "", "after": 0, "limit": 50}
    )
    assert notes.structured_content is not None
    assert notes.structured_content["items"][0]["source"] == "human"


@pytest.mark.anyio
async def test_human_operation_rejects_saved_run_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dispenser_conditioning_mcp import run_directory

    monkeypatch.setattr(run_directory, "RUNS_DIRECTORY", tmp_path)
    backend = SequenceBackend()
    service = _service(tmp_path, backend)
    saved = tmp_path / "saved"
    SessionRecorder(
        saved,
        source="scripted",
        session_kind="format_fixture",
        label="Saved",
    )
    result = await service.human_operation(
        HumanManualOperation.model_validate(
            {
                "run": "saved",
                "operation": "shutdown",
                "action_context": {
                    "action": "Shutdown",
                    "reason_summary": "Selected saved run",
                },
            }
        )
    )
    assert result.is_error is True
    assert backend.calls == []


@pytest.mark.anyio
async def test_human_operation_requires_operator_control_authorization(
    tmp_path: Path,
) -> None:
    backend = SequenceBackend()
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="format_fixture",
            label="Learning fixture",
        )
    )
    service = LearningModeService(
        LearningModeConfiguration(enabled=True),
        recording,
        cast(Any, backend),
        acceptance_context="production_dispenser",
        control_enabled=False,
    )
    result = await service.human_operation(
        HumanManualOperation.model_validate(
            {
                "run": "",
                "operation": "shutdown",
                "action_context": {
                    "action": "Shutdown",
                    "reason_summary": "Control remains startup-disabled",
                },
            }
        )
    )
    assert result.is_error is True
    assert backend.calls == []


@pytest.mark.anyio
async def test_sampler_detection_and_human_baseline_update_are_serialized(
    tmp_path: Path,
) -> None:
    class StatefulBackend(SequenceBackend):
        def __init__(self) -> None:
            super().__init__()
            self.output_enabled = False

        async def execute(
            self, name: str, arguments: dict[str, Any] | None = None
        ) -> CallToolResult:
            self.calls.append((name, dict(arguments or {})))
            if name == "read_vacuum_pressure":
                return await super().execute(name, arguments)
            if name == "enable_dispenser_output":
                self.output_enabled = True
            state = _power_state().model_dump(mode="json")
            state["output_enabled"] = self.output_enabled
            if name == "read_dispenser_power_state":
                return _result(state)
            return _result({"action": name, "wrote_hardware": True, "state": state})

    backend = StatefulBackend()
    service = _service(tmp_path, backend)
    detection_started = asyncio.Event()
    release_detection = asyncio.Event()
    original_detect = service._detect_external_change  # pyright: ignore[reportPrivateUsage]

    async def delayed_detect(state: dict[str, Any]) -> None:
        detection_started.set()
        await release_detection.wait()
        await original_detect(state)

    service._detect_external_change = delayed_detect  # pyright: ignore[reportPrivateUsage]
    sample = asyncio.create_task(service.sample_once())
    await detection_started.wait()
    manual = asyncio.create_task(
        service.human_operation(
            HumanManualOperation.model_validate(
                {
                    "run": "",
                    "operation": "enable",
                    "parallel_connection_confirmation": "confirmed_parallel_ch1",
                    "action_context": {
                        "action": "Enable",
                        "reason_summary": "Authenticated operator request",
                    },
                }
            )
        )
    )
    await asyncio.sleep(0)
    assert not any(name == "enable_dispenser_output" for name, _ in backend.calls)
    release_detection.set()
    await sample
    result = await manual
    assert result.is_error is False
    await service.sample_once()

    events = [
        json.loads(line)
        for line in (service.recording.directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert not any(event["kind"] == "external_change_detected" for event in events)


@pytest.mark.anyio
async def test_simulation_learning_backend_never_supplies_future_elapsed_time() -> None:
    class Adapter:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def execute_unrecorded(
            self, name: str, arguments: dict[str, Any]
        ) -> CallToolResult:
            self.calls.append((name, arguments))
            return _result({"observed_at": datetime.now(UTC).isoformat()})

    adapter = Adapter()
    backend = SimulationLearningBackend(
        adapter,
        acceptance_context="production_dispenser",
        control_enabled=True,
    )
    await backend.execute("read_vacuum_pressure")
    assert adapter.calls == [("read_vacuum_pressure", {})]


@pytest.mark.anyio
async def test_learning_simulation_rejects_public_future_time_advance(
    tmp_path: Path,
) -> None:
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="simulated",
            label="Learning simulation fixture",
            observed_time_origin="2040-01-01T00:00:00Z",
        )
    )
    router, _ = build_runtime(
        HiddenSimulatorConfig(
            seed="learning-time-fixture",
            scenario="nominal_recovery",
            observer_file=str(tmp_path / "run" / "observer.jsonl"),
        )
    )
    adapter = RecordingAdapter(router, recording)
    acceptance_context = cast(
        PowerAcceptanceContext, router.simulator.config.acceptance_context
    )
    backend = SimulationLearningBackend(
        adapter,
        acceptance_context=acceptance_context,
        control_enabled=router.simulator.config.control_enabled,
    )
    learning = LearningModeService(
        LearningModeConfiguration(enabled=True),
        recording,
        backend,
        acceptance_context=acceptance_context,
        control_enabled=router.simulator.config.control_enabled,
    )
    server = SimulationMCPServer(adapter, learning)
    before = router.simulator.state.virtual_time_s

    async with Client(server) as client:
        result = await client.call_tool("read_vacuum_pressure", {"elapsed_s": 10.0})

    assert result.is_error is True
    assert router.simulator.state.virtual_time_s == before


@pytest.mark.anyio
async def test_authenticated_human_sequence_uses_actual_simulation_adapter(
    tmp_path: Path,
) -> None:
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="simulated",
            label="Human learning control fixture",
            observed_time_origin="2040-01-01T00:00:00Z",
        )
    )
    router, _ = build_runtime(
        HiddenSimulatorConfig(
            seed="human-learning-control-fixture",
            scenario="nominal_recovery",
            observer_file=str(tmp_path / "run" / "observer.jsonl"),
        )
    )
    adapter = RecordingAdapter(router, recording)
    acceptance_context = cast(
        PowerAcceptanceContext, router.simulator.config.acceptance_context
    )
    learning = LearningModeService(
        LearningModeConfiguration(enabled=True, sample_interval_s=3600.0),
        recording,
        SimulationLearningBackend(
            adapter,
            acceptance_context=acceptance_context,
            control_enabled=True,
        ),
        acceptance_context=acceptance_context,
        control_enabled=True,
    )
    app = create_http_app(
        SimulationMCPServer(adapter, learning), McpTransportConfiguration()
    )
    access = app.state.dashboard_access
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8000",
        ) as client,
    ):
        assert (
            await client.post(
                "/dashboard/login",
                data={"code": access.token},
                headers={"Accept": "application/json"},
            )
        ).status_code == 200
        common = {
            "run": "",
            "action_context": {
                "action": "Authenticated human operation",
                "reason_summary": "Regression exercise through the real adapter",
            },
        }
        prepare = await client.post(
            "/api/learning/manual-operation",
            json={**common, "operation": "prepare"},
        )
        enable = await client.post(
            "/api/learning/manual-operation",
            json={
                **common,
                "operation": "enable",
                "parallel_connection_confirmation": "confirmed_parallel_ch1",
            },
        )
        current = await client.post(
            "/api/learning/manual-operation",
            json={
                **common,
                "operation": "set_current",
                "target_current_a": 0.2,
                "expected_current_a": 0.0,
            },
        )

    assert [prepare.status_code, enable.status_code, current.status_code] == [
        200,
        200,
        200,
    ]
    assert all(item.json()["ok"] is True for item in (prepare, enable, current))


def test_manual_operation_rejects_nonmatching_fields_before_backend() -> None:
    request = HumanManualOperation.model_validate(
        {
            "operation": "prepare",
            "action_context": {"action": "Prepare", "reason_summary": "Human"},
            "target_current_a": 0.2,
        }
    )
    service = object.__new__(LearningModeService)
    with pytest.raises(ValueError, match="do not match"):
        service._manual_arguments(request)  # pyright: ignore[reportPrivateUsage]


def test_review_schema_rejects_oversized_guidance_change_payload() -> None:
    request = RunReviewRequest.model_validate(
        {
            "run_key": "",
            "review_id": "id",
            "report_summary": "summary",
            "change_summary": "change",
            "source_ranges": [{"first_event_id": "a", "last_event_id": "b"}],
            "guidance_revision_refs": [],
            "guidance_changes": [],
        }
    )
    assert request.report_markdown is None
