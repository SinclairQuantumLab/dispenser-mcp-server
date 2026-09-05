from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from dispenser_conditioning_mcp.domain import (
    PressureObservationError,
    RawPressureObservation,
)
from dispenser_conditioning_mcp.power_domain import (
    DispenserPowerState,
    EnableConfirmation,
    NoLoadTestInterlockState,
    PowerAcceptanceContext,
    PowerActionResult,
    PowerControlError,
    PowerSafetyLimits,
)
from dispenser_conditioning_mcp.recording_service import RecordingService
from dispenser_conditioning_mcp.server import create_server
from dispenser_conditioning_mcp.session_records import SessionRecorder


@pytest.fixture(autouse=True)
def recording_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recording = RecordingService(
        SessionRecorder(
            tmp_path / "session",
            source="scripted",
            session_kind="format_fixture",
            label="Protocol fake fixture",
        )
    )
    monkeypatch.setattr(
        "dispenser_conditioning_mcp.server.RecordingService", lambda: recording
    )


def action_context(server: Any) -> dict[str, Any]:
    return {
        "session_id": server.recording.session_id,
        "decision_at": "2026-09-05T12:00:00Z",
        "action": "Explicit protocol test",
        "background": "Fake controller only",
        "rationale_summary": "Verify the selected call contract",
        "observation_ids": [],
        "confidence": {"claim": "This is a format fixture", "value": 1.0},
    }


class FakePressureSource:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def read(self) -> RawPressureObservation:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return RawPressureObservation(
            observed_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
            pressure_mbar=1.0e-7,
            p1_drive_serial_number="TC80-123",
        )


class FakePowerController:
    def __init__(
        self,
        *,
        acceptance_context: PowerAcceptanceContext = "production_dispenser",
        error: PowerControlError | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[float, float] | None]] = []
        self.acceptance_context: PowerAcceptanceContext = acceptance_context
        self.error = error

    def read_state(self) -> DispenserPowerState:
        self.calls.append(("read", None))
        self._raise_if_needed()
        return _power_state(self.acceptance_context)

    def prepare(self) -> PowerActionResult:
        return self._action("prepare_dispenser_power", "prepare")

    def enable(self, *, confirmation: EnableConfirmation) -> PowerActionResult:
        expected = (
            "confirmed_parallel_ch1"
            if self.acceptance_context == "production_dispenser"
            else "confirmed_no_dispenser_or_unapproved_load_connected"
        )
        assert confirmation == expected
        return self._action("enable_dispenser_output", "enable")

    def set_current(
        self, *, target_current_a: float, expected_current_a: float
    ) -> PowerActionResult:
        self.calls.append(("set", (target_current_a, expected_current_a)))
        self._raise_if_needed()
        return PowerActionResult(
            action="set_dispenser_current",
            wrote_hardware=False,
            state=_power_state(self.acceptance_context),
        )

    def shutdown(self) -> PowerActionResult:
        return self._action("shutdown_dispenser_power", "shutdown")

    def _action(
        self,
        action: str,
        call: str,
    ) -> PowerActionResult:
        self.calls.append((call, None))
        self._raise_if_needed()
        return PowerActionResult.model_validate(
            {
                "action": action,
                "wrote_hardware": True,
                "state": _power_state(self.acceptance_context),
            }
        )

    def _raise_if_needed(self) -> None:
        if self.error is not None:
            raise self.error


def _power_state(
    acceptance_context: PowerAcceptanceContext = "production_dispenser",
) -> DispenserPowerState:
    confirmation = (
        "confirmed_parallel_ch1"
        if acceptance_context == "production_dispenser"
        else "confirmed_no_dispenser_or_unapproved_load_connected"
    )
    return DispenserPowerState(
        observed_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        source="siglent_spd3000.semantic_driver",
        configured_topology="parallel_ch1",
        load_current_factor=2,
        expected_operating_mode="parallel",
        live_operating_mode="parallel",
        topology_matches=True,
        selected_native_channel="CH1",
        manufacturer="Siglent Technologies",
        model="SPD3303X",
        serial_number="SPD-TEST",
        firmware_version="1.0",
        native_voltage_setpoint_v=10.0,
        native_current_setpoint_a=0.1,
        commanded_load_current_limit_a=0.2,
        measured_native_channel_voltage_v=9.9,
        measured_native_channel_current_a=0.1,
        measured_native_channel_power_w=0.99,
        output_enabled=True,
        regulation_mode="CC",
        compliance_voltage_matches=True,
        prepared_for_enable=False,
        no_load_test_interlock=NoLoadTestInterlockState(
            applicable=acceptance_context == "no_load_test",
            status=(
                "unlatched"
                if acceptance_context == "no_load_test"
                else "not_applicable"
            ),
            trip=None,
            validation_status=(
                "offline_simulation_only_not_retested_on_physical_instrument"
                if acceptance_context == "no_load_test"
                else "not_applicable"
            ),
        ),
        safety_limits=PowerSafetyLimits(
            control_enabled=True,
            acceptance_context=acceptance_context,
            required_enable_confirmation=confirmation,
            fixed_compliance_voltage_v=10.0,
            operator_max_load_current_a=4.8,
            effective_max_load_current_a=4.8,
            deployment_native_current_ceiling_a=3.2,
            deployment_commanded_load_current_ceiling_a=6.4,
            workflow_absolute_current_ceiling_a=6.4,
            topology_hardware_current_ceiling_a=6.4,
            upward_step_a=0.2,
            native_voltage_resolution_v=0.001,
            native_current_resolution_a=0.001,
            no_load_test_safe_measured_current_abs_a=0.001,
        ),
        driver_hardware_validation_status=(
            "validated_on_physical_instrument_via_gateway"
        ),
        mcp_read_path_validation_status=(
            "validated_on_physical_instrument_via_authenticated_gateway"
        ),
        mcp_actuation_validation_status=(
            "not_yet_validated_with_connected_dispenser"
            if acceptance_context == "production_dispenser"
            else ("validated_on_unloaded_physical_instrument_via_authenticated_gateway")
        ),
    )


@pytest.mark.anyio
async def test_tool_catalog_is_closed_and_annotations_are_conservative() -> None:
    server = create_server(FakePressureSource(), FakePowerController())
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    assert [tool.name for tool in tools] == [
        "reload_dispenser_current_limit",
        "read_vacuum_pressure",
        "read_dispenser_power_state",
        "prepare_dispenser_power",
        "enable_dispenser_output",
        "set_dispenser_current",
        "shutdown_dispenser_power",
        "record_conditioning_decision",
    ]
    for tool in tools:
        assert tool.input_schema["additionalProperties"] is False
        rendered_schema = str(tool.input_schema).lower()
        assert "trip_latch" not in rendered_schema
        assert "latch_path" not in rendered_schema
        assert "acceptance_context" not in rendered_schema
        assert "reset" not in rendered_schema
        assert "bypass" not in rendered_schema
        for startup_only_name in (
            "transport",
            "bind_host",
            "http_port",
            "http_path",
            "trust_mode",
            "allowed_hosts",
            "allowed_origins",
            "credential",
        ):
            assert startup_only_name not in rendered_schema

    by_name = {tool.name: tool for tool in tools}

    def annotations(name: str):  # type: ignore[no-untyped-def]
        result = by_name[name].annotations
        assert result is not None
        return result

    assert by_name["read_vacuum_pressure"].input_schema["properties"] == {}
    set_schema = by_name["set_dispenser_current"].input_schema
    assert set(set_schema["properties"]) == {
        "target_current_a",
        "expected_current_a",
        "action_context",
    }
    assert set(set_schema["required"]) == {
        "target_current_a",
        "expected_current_a",
        "action_context",
    }
    assert set_schema["properties"]["target_current_a"]["maximum"] == 6.4
    enable_schema = by_name["enable_dispenser_output"].input_schema
    assert set(enable_schema["required"]) == {
        "parallel_connection_confirmation",
        "action_context",
    }
    assert enable_schema["properties"]["parallel_connection_confirmation"]["const"] == (
        "confirmed_parallel_ch1"
    )
    state_output_schema = by_name["read_dispenser_power_state"].output_schema
    assert state_output_schema is not None
    safety_limits_schema = state_output_schema["$defs"]["PowerSafetyLimits"]
    safe_band_schema = safety_limits_schema["properties"][
        "no_load_test_safe_measured_current_abs_a"
    ]
    assert safe_band_schema["minimum"] == 0.001
    assert safe_band_schema["maximum"] == 0.001
    trip_schema = state_output_schema["$defs"]["NoLoadTestTripRecord"]
    assert trip_schema["anyOf"] == [
        {"$ref": "#/$defs/OutsideBandNoLoadTestTripRecord"},
        {"$ref": "#/$defs/UnavailableNoLoadTestTripRecord"},
    ]
    outside_current_schema = state_output_schema["$defs"][
        "OutsideBandNoLoadTestTripRecord"
    ]["properties"]["observed_native_channel_current_a"]
    assert outside_current_schema["anyOf"] == [
        {"exclusiveMaximum": -0.001, "type": "number"},
        {"exclusiveMinimum": 0.001, "type": "number"},
    ]
    unavailable_current_schema = state_output_schema["$defs"][
        "UnavailableNoLoadTestTripRecord"
    ]["properties"]["observed_native_channel_current_a"]
    assert unavailable_current_schema["type"] == "null"
    interlock = state_output_schema["$defs"]["NoLoadTestInterlockState"]["properties"]
    assert "failure_reason" not in interlock
    assert "reset_authority" not in interlock
    assert interlock["status"]["enum"] == ["not_applicable", "unlatched", "latched"]

    assert annotations("read_dispenser_power_state").read_only_hint is True
    assert annotations("prepare_dispenser_power").destructive_hint is True
    assert annotations("prepare_dispenser_power").idempotent_hint is True
    assert annotations("enable_dispenser_output").destructive_hint is True
    assert annotations("enable_dispenser_output").idempotent_hint is False
    assert annotations("set_dispenser_current").destructive_hint is True
    assert annotations("set_dispenser_current").idempotent_hint is False
    assert annotations("shutdown_dispenser_power").destructive_hint is True
    assert annotations("shutdown_dispenser_power").idempotent_hint is True
    assert all(
        annotations(tool.name).open_world_hint is True
        for tool in tools
        if tool.name != "reload_dispenser_current_limit"
    )
    assert annotations("reload_dispenser_current_limit").open_world_hint is False


@pytest.mark.anyio
async def test_read_vacuum_pressure_preserves_structured_contract() -> None:
    source = FakePressureSource()
    server = create_server(source, FakePowerController())

    async with Client(server) as client:
        result = await client.call_tool("read_vacuum_pressure", {})

    assert result.is_error is False
    assert source.calls == 1
    assert result.structured_content == {
        "observed_at": "2026-09-03T12:00:00Z",
        "pressure_mbar": 1.0e-7,
        "pressure_torr": pytest.approx(1.0e-7 * 760 / 1013.25),
        "source": "pfeiffer_hicube_neo.pvviewer.g1_pressure",
        "p1_drive_serial_number": "TC80-123",
        "is_total_gauge_pressure": True,
        "is_rubidium_partial_pressure": False,
        "verifies_dispenser_activation": False,
    }


@pytest.mark.anyio
async def test_set_current_forwards_compare_and_set_values() -> None:
    controller = FakePowerController()
    server = create_server(FakePressureSource(), controller)

    async with Client(server) as client:
        result = await client.call_tool(
            "set_dispenser_current",
            {
                "target_current_a": 0.2,
                "expected_current_a": 0.1,
                "action_context": action_context(server),
            },
        )

    assert result.is_error is False
    assert controller.calls == [("set", (0.2, 0.1))]
    assert result.structured_content is not None
    assert result.structured_content["action"] == "set_dispenser_current"
    state = result.structured_content["state"]
    assert state["commanded_load_current_limit_a"] == 0.2
    assert state["measured_native_channel_current_a"] == 0.1
    assert "measured_load_current_a" not in state
    assert state["no_load_test_interlock"]["status"] == "not_applicable"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_current_a", "0.2"),
        ("expected_current_a", "0.1"),
        ("target_current_a", True),
        ("expected_current_a", False),
    ],
)
async def test_set_current_rejects_non_json_numbers_before_controller(
    field: str,
    value: object,
) -> None:
    controller = FakePowerController()
    arguments: dict[str, object] = {
        "target_current_a": 0.2,
        "expected_current_a": 0.1,
    }
    arguments[field] = value
    server = create_server(FakePressureSource(), controller)

    async with Client(server) as client:
        arguments["action_context"] = action_context(server)
        result = await client.call_tool("set_dispenser_current", arguments)

    assert result.is_error is True
    assert controller.calls == []


@pytest.mark.anyio
async def test_set_current_accepts_json_integer_and_float_numbers() -> None:
    controller = FakePowerController()
    server = create_server(FakePressureSource(), controller)

    async with Client(server) as client:
        result = await client.call_tool(
            "set_dispenser_current",
            {
                "target_current_a": 0,
                "expected_current_a": 0.0,
                "action_context": action_context(server),
            },
        )

    assert result.is_error is False
    assert controller.calls == [("set", (0.0, 0.0))]


@pytest.mark.anyio
async def test_enable_requires_fresh_parallel_confirmation() -> None:
    controller = FakePowerController()
    server = create_server(FakePressureSource(), controller)

    async with Client(server) as client:
        missing = await client.call_tool("enable_dispenser_output", {})
        wrong_context = await client.call_tool(
            "enable_dispenser_output",
            {
                "action_context": action_context(server),
                "no_load_test_connection_confirmation": (
                    "confirmed_no_dispenser_or_unapproved_load_connected"
                ),
            },
        )
        wrong_literal = await client.call_tool(
            "enable_dispenser_output",
            {
                "action_context": action_context(server),
                "parallel_connection_confirmation": (
                    "confirmed_no_dispenser_or_unapproved_load_connected"
                ),
            },
        )
        confirmed = await client.call_tool(
            "enable_dispenser_output",
            {
                "parallel_connection_confirmation": "confirmed_parallel_ch1",
                "action_context": action_context(server),
            },
        )

    assert missing.is_error is True
    assert wrong_context.is_error is True
    assert wrong_literal.is_error is True
    assert confirmed.is_error is False
    assert controller.calls == [("enable", None)]


@pytest.mark.anyio
async def test_no_load_test_enable_schema_cannot_reuse_production_confirmation() -> (
    None
):
    controller = FakePowerController(acceptance_context="no_load_test")
    server = create_server(FakePressureSource(), controller)

    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        production_confirmation = await client.call_tool(
            "enable_dispenser_output",
            {"parallel_connection_confirmation": "confirmed_parallel_ch1"},
        )
        production_literal = await client.call_tool(
            "enable_dispenser_output",
            {
                "no_load_test_connection_confirmation": "confirmed_parallel_ch1",
                "action_context": action_context(server),
            },
        )
        unloaded_confirmation = await client.call_tool(
            "enable_dispenser_output",
            {
                "action_context": action_context(server),
                "no_load_test_connection_confirmation": (
                    "confirmed_no_dispenser_or_unapproved_load_connected"
                ),
            },
        )

    enable_schema = tools["enable_dispenser_output"].input_schema
    assert set(enable_schema["required"]) == {
        "no_load_test_connection_confirmation",
        "action_context",
    }
    assert enable_schema["properties"]["no_load_test_connection_confirmation"][
        "const"
    ] == ("confirmed_no_dispenser_or_unapproved_load_connected")
    assert "parallel_connection_confirmation" not in enable_schema["properties"]
    assert production_confirmation.is_error is True
    assert production_literal.is_error is True
    assert unloaded_confirmation.is_error is False
    assert controller.calls == [("enable", None)]


@pytest.mark.anyio
async def test_unknown_argument_is_rejected_before_any_integration_call() -> None:
    pressure = FakePressureSource()
    power = FakePowerController()
    server = create_server(pressure, power)

    async with Client(server) as client:
        result = await client.call_tool(
            "set_dispenser_current",
            {
                "target_current_a": 0.2,
                "expected_current_a": 0.1,
                "channel": "CH2",
            },
        )

    assert result.is_error is True
    assert pressure.calls == 0
    assert power.calls == []
    text = " ".join(
        block.text for block in result.content if isinstance(block, TextContent)
    )
    assert "unsupported argument" in text
    assert "CH2" not in text


@pytest.mark.anyio
async def test_source_errors_are_sanitized() -> None:
    pressure_secret = "opc.tcp://private-host:4840/internal/path"
    pressure = FakePressureSource(error=PressureObservationError(pressure_secret))
    power_secret = "TCPIP::private-supply"
    power = FakePowerController(
        error=PowerControlError(
            "Power-supply state is unavailable from the configured source."
        )
    )
    server = create_server(pressure, power)

    async with Client(server) as client:
        pressure_result = await client.call_tool("read_vacuum_pressure", {})
        power_result = await client.call_tool("read_dispenser_power_state", {})

    pressure_text = " ".join(
        block.text
        for block in pressure_result.content
        if isinstance(block, TextContent)
    )
    power_text = " ".join(
        block.text for block in power_result.content if isinstance(block, TextContent)
    )
    assert pressure_result.is_error is True
    assert power_result.is_error is True
    assert pressure_secret not in pressure_text
    assert power_secret not in power_text
    assert "unavailable" in pressure_text
    assert "unavailable" in power_text
