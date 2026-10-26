from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from mcp import Client
from mcp.types import CallToolResult
from test_protocol import FakePowerController, FakePressureSource

from dispenser_conditioning_mcp.config import SourceLayout
from dispenser_conditioning_mcp.learning_mode import (
    HumanActionContext,
    HumanManualOperation,
    LearningModeService,
    SerializedHardwareBackend,
)
from dispenser_conditioning_mcp.operator_settings import (
    OperatorConsoleSettings,
    OperatorSettingsStore,
    OperatorSettingsUpdate,
)
from dispenser_conditioning_mcp.recording_service import RecordingService
from dispenser_conditioning_mcp.runtime_coordinator import (
    RuntimeCoordinator,
    StartRunRequest,
    StopRunRequest,
    UpdateRunRequest,
    bootstrap_transport,
    create_runtime_http_app,
)
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.session_records import SessionRecorder
from dispenser_conditioning_mcp.transport import McpTransportConfiguration


def _settings(*, authority: str = "agent_control") -> OperatorConsoleSettings:
    return OperatorConsoleSettings.model_validate(
        {
            "backend": "hardware",
            "authority_mode": authority,
            "control_enabled": True,
            "acceptance_context": "production_dispenser",
            "expected_serial_number": "SPD-TEST",
            "compliance_voltage_v": 1.0,
            "max_load_current_A": 4.8,
            "sample_interval_s": 3600.0,
            "hardware": {
                "hicube_host": "offline.invalid",
                "hicube_port": 4840,
                "hicube_timeout_s": 1.0,
                "siglent_identifier": "offline.invalid:8765",
                "siglent_timeout_s": 1.0,
                "siglent_minimum_command_interval_ms": 100.0,
            },
            "simulation": {
                "seed": "fixture-seed",
                "scenario": "nominal_recovery",
            },
        }
    )


def _saved_store(
    tmp_path: Path, settings: OperatorConsoleSettings
) -> OperatorSettingsStore:
    layout = SourceLayout._for_testing(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "checkout"
    )
    store = OperatorSettingsStore(layout)
    store.save(
        OperatorSettingsUpdate(expected_revision=None, settings=settings), active=False
    )
    return store


def _runtime(
    tmp_path: Path, *, authority: str = "agent_control"
) -> tuple[RuntimeCoordinator, FakePowerController, list[str], list[str]]:
    store = _saved_store(tmp_path, _settings(authority=authority))
    controller = FakePowerController()
    checks: list[str] = []
    starts: list[str] = []

    async def check(settings: OperatorConsoleSettings) -> tuple[dict[str, Any], ...]:
        checks.append(settings.backend)
        return (
            {"name": "pressure_source", "ok": True},
            {"name": "power_source", "ok": True},
        )

    async def start(settings: OperatorConsoleSettings):
        starts.append(settings.backend)
        directory = tmp_path / f"run-{len(starts)}"
        recording = RecordingService(
            SessionRecorder(
                directory,
                source="scripted",
                session_kind="format_fixture",
                label="Runtime coordinator fixture",
            )
        )
        backend = SerializedHardwareBackend(
            FakePressureSource(), controller, control_enabled=settings.control_enabled
        )
        learning = LearningModeService(
            settings.learning,
            recording,
            backend,
            acceptance_context="production_dispenser",
            control_enabled=settings.control_enabled,
        )
        return create_server(
            backend,
            backend,
            recording=recording,
            learning=learning,
            initial_max_load_current_A=settings.max_load_current_A,
        )

    return (
        RuntimeCoordinator(
            layout=store.layout, settings_store=store, checker=check, starter=start
        ),
        controller,
        checks,
        starts,
    )


@pytest.mark.anyio
async def test_idle_check_start_and_stop_are_explicit_and_do_not_actuate(
    tmp_path: Path,
) -> None:
    coordinator, controller, checks, starts = _runtime(tmp_path)
    settings = coordinator.operator_settings()
    revision = settings["revision"]
    assert coordinator.public_status()["lifecycle"] == "idle"
    assert checks == starts == []
    assert controller.calls == []

    checked = await coordinator.check(revision)
    assert checked["check_id"]
    assert checks == ["hardware"]
    assert starts == []
    assert controller.calls == []

    started = await coordinator.start_run(
        StartRunRequest(revision=revision, check_id=checked["check_id"])
    )
    assert started["lifecycle"] == "running"
    assert started["active_run"]["authority_mode"] == "agent_control"
    assert started["sampler"]["running"] is True
    binding = started["active_run"]["run_binding"]

    stopped = await coordinator.stop_run(
        StopRunRequest(
            run_binding=binding,
            acknowledge_no_automatic_output_change=True,
        )
    )
    assert stopped["lifecycle"] == "idle"
    assert stopped["agent_permissions"]["instrument_mutations"] is False
    assert not any(
        name in {"prepare", "enable", "set", "shutdown"} for name, _ in controller.calls
    )
    before_idle_read = list(controller.calls)
    idle_read = cast(
        CallToolResult,
        await coordinator.mcp.call_tool("read_dispenser_power_state", {}),
    )
    assert idle_read.is_error is True
    assert controller.calls == before_idle_read

    history = cast(
        CallToolResult,
        await coordinator.mcp.call_tool("list_conditioning_runs", {}),
    )
    assert history.is_error is False
    assert history.structured_content["runs"][0]["current"] is False
    learning = coordinator.learning_snapshot(
        run_key="",
        review_after=0,
        review_limit=50,
        note_after=0,
        note_limit=50,
        operator_authorized=True,
    )
    assert learning["current"] is False
    assert learning["running"] is False
    assert learning["run_binding"] is None


@pytest.mark.anyio
async def test_stale_binding_and_human_authority_reject_agent_shutdown_before_dispatch(
    tmp_path: Path,
) -> None:
    coordinator, controller, _, _ = _runtime(tmp_path)
    revision = coordinator.operator_settings()["revision"]
    checked = await coordinator.check(revision)
    started = await coordinator.start_run(
        StartRunRequest(revision=revision, check_id=checked["check_id"])
    )
    binding = started["active_run"]["run_binding"]
    baseline = list(controller.calls)

    stale = cast(
        CallToolResult,
        await coordinator.mcp.call_tool(
            "shutdown_dispenser_power", {"run_binding": "stale"}
        ),
    )
    assert stale.is_error is True
    assert controller.calls == baseline

    updated = await coordinator.update_run(
        UpdateRunRequest(
            run_binding=binding,
            authority_mode="human_learning",
            sample_interval_s=1800.0,
        )
    )
    assert updated["active_run"]["run_binding"] == binding
    assert updated["agent_permissions"]["instrument_mutations"] is False
    denied = cast(
        CallToolResult,
        await coordinator.mcp.call_tool(
            "shutdown_dispenser_power", {"run_binding": binding}
        ),
    )
    assert denied.is_error is True
    assert controller.calls == baseline
    await coordinator.shutdown_lifecycle()


@pytest.mark.anyio
async def test_one_connected_sdk_client_refreshes_tool_catalog_after_idle_start(
    tmp_path: Path,
) -> None:
    coordinator, _, _, _ = _runtime(tmp_path)
    async with Client(coordinator.mcp) as client:
        idle = await client.list_tools()
        assert [tool.name for tool in idle.tools] == ["read_server_status"]
        status = await client.call_tool("read_server_status", {})
        assert status.structured_content["lifecycle"] == "idle"

        revision = coordinator.operator_settings()["revision"]
        checked = await coordinator.check(revision)
        await coordinator.start_run(
            StartRunRequest(revision=revision, check_id=checked["check_id"])
        )
        active = await client.list_tools()
        shutdown = next(
            tool for tool in active.tools if tool.name == "shutdown_dispenser_power"
        )
        assert "run_binding" in shutdown.input_schema["required"]
        assert "reload_dispenser_current_limit" not in {
            tool.name for tool in active.tools
        }
    await coordinator.shutdown_lifecycle()


@pytest.mark.anyio
async def test_cold_idle_exposes_saved_history_without_allocating_backend(
    tmp_path: Path,
) -> None:
    store = _saved_store(tmp_path, _settings())
    directory = store.layout.project_root / "runs" / "2026-10-26_saved_fixture"
    recorder = SessionRecorder(
        directory,
        source="scripted",
        session_kind="format_fixture",
        label="Cold saved fixture",
    )
    RecordingService(recorder).record_learning_event("fixture", {"source": "scripted"})
    starts: list[str] = []

    async def start(_settings: OperatorConsoleSettings):
        starts.append("called")
        raise AssertionError("saved-run reads must not allocate a backend")

    coordinator = RuntimeCoordinator(
        layout=store.layout, settings_store=store, starter=start
    )
    before = sorted(path.name for path in directory.parent.iterdir())

    async with Client(coordinator.mcp) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {
            "read_server_status",
            "list_conditioning_runs",
            "read_conditioning_run",
            "read_saved_simulation_state",
            "append_conditioning_run_review",
            "read_conditioning_run_reviews",
            "read_conditioning_run_notes",
        }.issubset(names)
        assert "read_vacuum_pressure" not in names
        listed = await client.call_tool("list_conditioning_runs", {})
        reviews = await client.call_tool(
            "read_conditioning_run_reviews",
            {"run_key": "", "after": 0, "limit": 50},
        )

    assert listed.is_error is False
    assert listed.structured_content["runs"][0]["current"] is False
    assert reviews.is_error is False
    assert reviews.structured_content == {
        "items": [],
        "after": 0,
        "has_more": False,
    }
    assert starts == []
    assert sorted(path.name for path in directory.parent.iterdir()) == before


@pytest.mark.anyio
async def test_human_operation_requires_explicit_takeover_from_agent_authority(
    tmp_path: Path,
) -> None:
    coordinator, controller, _, _ = _runtime(tmp_path)
    revision = coordinator.operator_settings()["revision"]
    checked = await coordinator.check(revision)
    started = await coordinator.start_run(
        StartRunRequest(revision=revision, check_id=checked["check_id"])
    )
    binding = started["active_run"]["run_binding"]
    base = {
        "run_binding": binding,
        "operation": "prepare",
        "action_context": HumanActionContext(
            action="prepare", reason_summary="Operator selected prepare."
        ),
    }
    before = list(controller.calls)
    rejected = await coordinator.human_operation(
        HumanManualOperation.model_validate(base)
    )
    assert rejected.is_error is True
    assert coordinator.active is not None
    assert coordinator.active.authority_mode == "agent_control"
    assert controller.calls == before

    accepted = await coordinator.human_operation(
        HumanManualOperation.model_validate(
            {
                **base,
                "operator_takeover_confirmation": "confirmed_operator_takeover",
            }
        )
    )
    assert accepted.is_error is False
    assert coordinator.active is not None
    assert coordinator.active.authority_mode == "human_learning"
    learning = coordinator.learning_snapshot(
        run_key="",
        review_after=0,
        review_limit=50,
        note_after=0,
        note_limit=50,
        operator_authorized=True,
    )
    assert learning["enabled"] is True
    assert learning["human_control_available"] is True
    assert ("prepare", None) in controller.calls
    await coordinator.shutdown_lifecycle()


@pytest.mark.anyio
async def test_failed_check_allocates_no_run_and_http_stays_available(
    tmp_path: Path,
) -> None:
    store = _saved_store(tmp_path, _settings())
    starts: list[str] = []

    async def fail(_settings: OperatorConsoleSettings) -> tuple[dict[str, Any], ...]:
        raise RuntimeError("private endpoint details")

    async def start(_settings: OperatorConsoleSettings):
        starts.append("called")
        raise AssertionError("must not start")

    coordinator = RuntimeCoordinator(
        layout=store.layout, settings_store=store, checker=fail, starter=start
    )
    app = create_runtime_http_app(coordinator, McpTransportConfiguration())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runtime")
        assert response.json()["lifecycle"] == "idle"
        response = await client.get("/console")
        assert response.status_code == 200
        cookie = {"dispenser_dashboard": coordinator.access.cookie}
        response = await client.post(
            "/api/operator/check",
            json={"revision": coordinator.operator_settings()["revision"]},
            cookies=cookie,
        )
        assert response.status_code == 409
        assert "private endpoint" not in response.text
        assert (await client.get("/api/runtime")).json()["lifecycle"] == "idle"
    assert starts == []


def test_bootstrap_transport_allows_missing_instrument_documents_but_is_strict(
    tmp_path: Path,
) -> None:
    layout = SourceLayout._for_testing(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "checkout"
    )
    assert bootstrap_transport(layout) == McpTransportConfiguration()
    layout.settings_directory.mkdir(parents=True)
    layout.mcp_settings_file.write_text(
        "allow_remote_access = true\nport = 8123\n", encoding="utf-8"
    )
    assert bootstrap_transport(layout) == McpTransportConfiguration(True, 8123)
    layout.mcp_settings_file.write_text(
        'allow_remote_access = "yes"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Bootstrap"):
        bootstrap_transport(layout)


def test_unsaved_console_settings_are_suggestions_not_runtime_configuration(
    tmp_path: Path,
) -> None:
    layout = SourceLayout._for_testing(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "checkout"
    )
    store = OperatorSettingsStore(layout)
    settings, revision = store.load()
    suggestion = store.suggested()
    assert settings is None
    assert revision == "absent"
    assert suggestion["backend"] == "hardware"
    assert suggestion["simulation"] == {
        "seed": "operator-console-default",
        "scenario": "nominal_recovery",
    }
    assert not store.path.exists()


def test_simulation_settings_do_not_require_invented_hardware_identity() -> None:
    value = _settings().model_dump(mode="json")
    value.update({"backend": "simulation", "expected_serial_number": ""})
    value["hardware"].update({"hicube_host": "", "siglent_identifier": ""})

    settings = OperatorConsoleSettings.model_validate(value)

    assert settings.backend == "simulation"
    assert settings.simulator_configuration().scenario == "nominal_recovery"
