import asyncio
import json
import socket

import pytest
import uvicorn
from mcp import Client
from test_config import _write_layout
from test_server_recording import context
from test_siglent import FakeDevice, controller

from dispenser_conditioning_mcp.config import (
    ConfigurationError,
    OperatorConfiguration,
    SourceLayout,
    parse_max_load_current,
)
from dispenser_conditioning_mcp.current_limit import RELOAD_CURRENT_LIMIT_TOOL
from dispenser_conditioning_mcp.power_domain import PowerControlError
from dispenser_conditioning_mcp.recording_service import RecordingService
from dispenser_conditioning_mcp.session_records import SessionRecorder
from dispenser_conditioning_mcp.simulation_app import SimulationMCPServer
from dispenser_conditioning_mcp.transport import (
    McpTransportConfiguration,
    create_http_app,
)
from dispenser_simulator.model import (
    HiddenSimulatorConfig,
    SimulatedDispenser,
    ToolRouter,
)
from dispenser_simulator.recording import RecordingAdapter


@pytest.mark.parametrize(
    "bad", [None, True, "0.4", 0, -1, 6.41, 10.0, float("nan"), float("inf")]
)
def test_operator_cap_is_strict(bad):
    with pytest.raises(ConfigurationError):
        parse_max_load_current({"max_load_current_A": bad})


def test_startup_cap_default_and_operator_setting(tmp_path):
    assert parse_max_load_current({}) == 4.8
    layout = _write_layout(tmp_path, main_extra="max_load_current_A = 0.6\n")
    operator = OperatorConfiguration.from_toml(layout)
    assert (
        operator.startup.max_load_current_A
        == operator.siglent.max_load_current_a
        == 0.6
    )


def test_hardware_reload_no_io_lowering_enforcement_and_invalid_preserves(tmp_path):
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = 0.2  # Existing combined load setting 0.4 A.
    power, _ = controller(tmp_path, device)
    layout = SourceLayout._for_testing(tmp_path)
    layout.settings_directory.mkdir()
    layout.mcp_settings_file.write_text(
        'max_load_current_A=0.2\ncontrol_enabled=false\nbackend="simulation"\n'
    )
    applied = power.reload_current_limit(layout)
    assert applied.previous_max_load_current_A == 4.8
    assert applied.applied_max_load_current_A == 0.2
    assert applied.fresh_state_inspection_recommended and not applied.hardware_changed
    assert (
        device.events == []
        and device.output_enabled
        and device.current_setpoint_a == 0.2
    )
    with pytest.raises(PowerControlError, match="ceiling"):
        power.set_current(target_current_a=0.4, expected_current_a=0.4)
    assert device.events == []
    power.set_current(target_current_a=0.2, expected_current_a=0.4)
    assert device.current_setpoint_a == 0.1  # Other settings were NOT reloaded.
    for content in ("port=8000\n", "max_load_current_A=6.5\n", "broken = ["):
        layout.mcp_settings_file.write_text(content)
        with pytest.raises(ConfigurationError):
            power.reload_current_limit(layout)
        assert power.read_state().safety_limits.operator_max_load_current_a == 0.2
    layout.mcp_settings_file.write_text("max_load_current_A=0.6\n")
    assert power.reload_current_limit(layout).applied_max_load_current_A == 0.6
    power.set_current(target_current_a=0.4, expected_current_a=0.2)
    power.shutdown()
    assert not device.output_enabled


@pytest.mark.anyio
async def test_local_http_simulator_reload_discovery_and_recording(tmp_path):
    layout = SourceLayout._for_testing(tmp_path)
    layout.settings_directory.mkdir()
    layout.mcp_settings_file.write_text("max_load_current_A=0.2\n")
    sim = SimulatedDispenser(
        HiddenSimulatorConfig(
            seed="limit-fixture", scenario="nominal_recovery", max_load_current_a=0.4
        ),
        monotonic=lambda: 0,
    )
    service = RecordingService(
        SessionRecorder(
            tmp_path / "run",
            source="scripted",
            session_kind="simulated",
            label="Current cap HTTP fixture",
        )
    )
    server = SimulationMCPServer(
        RecordingAdapter(ToolRouter(sim), service, layout=layout)
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    listener = uvicorn.Server(
        uvicorn.Config(
            create_http_app(server, McpTransportConfiguration(port=port)),
            host="127.0.0.1",
            port=port,
            access_log=False,
            proxy_headers=False,
            log_level="error",
        )
    )
    task = asyncio.create_task(listener.serve())
    try:
        async with asyncio.timeout(15):
            while not listener.started:
                await asyncio.sleep(0.01)
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                tools = (await client.list_tools()).tools
                assert len(tools) == 11
                assert (
                    next(
                        t for t in tools if t.name == RELOAD_CURRENT_LIMIT_TOOL
                    ).input_schema["properties"]
                    == {}
                )
                set_schema = next(
                    t for t in tools if t.name == "set_dispenser_current"
                ).input_schema
                assert set_schema["properties"]["target_current_a"]["maximum"] == 6.4
                bad = await client.call_tool(
                    RELOAD_CURRENT_LIMIT_TOOL, {"max_load_current_A": 4.8}
                )
                assert bad.is_error
                result = await client.call_tool(RELOAD_CURRENT_LIMIT_TOOL, {})
                assert not result.is_error
                assert result.structured_content["previous_max_load_current_A"] == 0.4
                assert result.structured_content["applied_max_load_current_A"] == 0.2
                assert sim.state.virtual_time_s == 0 and not sim.state.ch1_output_on
                for name, arguments in [
                    ("prepare_dispenser_power", {}),
                    (
                        "enable_dispenser_output",
                        {"parallel_connection_confirmation": "confirmed_parallel_ch1"},
                    ),
                    (
                        "set_dispenser_current",
                        {"target_current_a": 0.2, "expected_current_a": 0},
                    ),
                ]:
                    result = await client.call_tool(
                        name, {**arguments, "action_context": context(service)}
                    )
                    assert not result.is_error
                result = await client.call_tool(
                    "set_dispenser_current",
                    {
                        "target_current_a": 0.4,
                        "expected_current_a": 0.2,
                        "action_context": context(service),
                    },
                )
                assert (
                    result.is_error and sim.state.native_ch1_current_setpoint_a == 0.1
                )
                layout.mcp_settings_file.write_text("max_load_current_A=0.4\n")
                assert not (
                    await client.call_tool(RELOAD_CURRENT_LIMIT_TOOL, {})
                ).is_error
                assert not (
                    await client.call_tool(
                        "set_dispenser_current",
                        {
                            "target_current_a": 0.4,
                            "expected_current_a": 0.2,
                            "action_context": context(service),
                        },
                    )
                ).is_error
                layout.mcp_settings_file.write_text("port=8000\n")
                assert (await client.call_tool(RELOAD_CURRENT_LIMIT_TOOL, {})).is_error
                assert sim.config.max_load_current_a == 0.4
                assert not (
                    await client.call_tool("shutdown_dispenser_power", {})
                ).is_error
                assert not sim.state.ch1_output_on
    finally:
        listener.should_exit = True
        await task
    records = [
        json.loads(line)
        for line in (service.directory / "events.jsonl").read_text().splitlines()
    ]
    assert any(
        r["kind"] == "call_intent"
        and r["payload"].get("tool") == RELOAD_CURRENT_LIMIT_TOOL
        for r in records
    )


def test_reload_display_result_preserved_in_compact_record():
    from dispenser_conditioning_mcp.current_limit import reload_result
    from dispenser_conditioning_mcp.dashboard import dashboard_record

    record = dashboard_record(
        {
            "event_id": "reload",
            "kind": "call_result",
            "payload": {
                "tool": RELOAD_CURRENT_LIMIT_TOOL,
                "execution": "completed",
                "result": {
                    "structuredContent": reload_result(4.8, 0.6).model_dump(mode="json")
                },
            },
        }
    )
    assert record["previous_max_load_current_A"] == 4.8
    assert record["applied_max_load_current_A"] == 0.6
    assert record["hardware_changed"] is False
    assert record["fresh_state_inspection_recommended"] is True
    assert "payload" not in record


@pytest.mark.parametrize("cap,start,target", [(5.0, 4.8, 5.0), (6.4, 6.2, 6.4)])
def test_raised_cap_respects_spd_range(tmp_path, cap, start, target):
    from dataclasses import replace

    from test_siglent import configuration

    from dispenser_simulator.model import SimulationError

    assert parse_max_load_current({"max_load_current_A": cap}) == cap
    device = FakeDevice()
    device.output_enabled = True
    device.current_setpoint_a = start / 2
    power, _ = controller(
        tmp_path,
        device,
        config=replace(configuration(tmp_path), max_load_current_a=cap),
    )
    power.set_current(target_current_a=target, expected_current_a=start)
    assert device.current_setpoint_a == target / 2
    assert power.read_state().safety_limits.effective_max_load_current_a == cap
    device.events.clear()
    with pytest.raises(PowerControlError):
        power.set_current(target_current_a=6.6, expected_current_a=target)
    assert device.events == []
    sim = SimulatedDispenser(
        HiddenSimulatorConfig(
            seed="cap-fixture", scenario="nominal_recovery", max_load_current_a=cap
        ),
        monotonic=lambda: 0,
    )
    sim.state.ch1_output_on = True
    sim.state.prepared = True
    sim.state.native_ch1_voltage_setpoint_v = sim.config.compliance_voltage_v
    sim.state.native_ch1_current_setpoint_a = start / 2
    sim.set_dispenser_current(target, start)
    assert sim.state.native_ch1_current_setpoint_a == target / 2
    with pytest.raises(SimulationError):
        sim.set_dispenser_current(6.6, target)
    assert sim.state.native_ch1_current_setpoint_a == target / 2
