"""Machine-facing MCP catalog for the simulator adapter."""

from __future__ import annotations

import copy
from typing import Any

from .metadata import NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A

READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

MUTATING_IDEMPOTENT_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

MUTATING_NONIDEMPOTENT_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


def action_context_schema() -> dict[str, Any]:
    """Session correlation and caller-stated context; not scientific scoring."""
    return {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "decision_at": {"type": "string", "format": "date-time"},
            "action": {"type": "string", "minLength": 1, "maxLength": 500},
            "background": {"type": "string", "minLength": 1, "maxLength": 4000},
            "rationale_summary": {"type": "string", "minLength": 1, "maxLength": 2000},
            "observation_ids": {
                "type": "array",
                "maxItems": 100,
                "items": {"type": "string", "minLength": 1, "maxLength": 128},
                "description": "Successfully recorded observation IDs from this session.",
            },
            "token_usage": {
                "type": ["object", "null"],
                "properties": {
                    "usage_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "total_tokens": {"type": "integer", "minimum": 0},
                    "input_tokens": {"type": ["integer", "null"], "minimum": 0},
                    "output_tokens": {"type": ["integer", "null"], "minimum": 0},
                    "cached_input_tokens": {"type": ["integer", "null"], "minimum": 0},
                    "model": {
                        "type": ["string", "null"],
                        "minLength": 1,
                        "maxLength": 128,
                    },
                },
                "required": ["usage_id", "total_tokens"],
                "additionalProperties": False,
                "description": "Optional caller-reported usage; repeat usage_id for the same accounting batch. Not independently verified. Input includes cached; output includes reasoning when provided.",
            },
            "confidence": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "value": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                },
                "required": ["claim", "value"],
                "additionalProperties": False,
            },
        },
        "required": [
            "session_id",
            "decision_at",
            "action",
            "background",
            "rationale_summary",
            "observation_ids",
            "confidence",
        ],
        "additionalProperties": False,
    }


def recording_tool_specs(acceptance_context: str) -> list[dict[str, Any]]:
    """Seven public adapter tools; the six underlying physics inputs stay closed."""
    catalog = copy.deepcopy(tool_specs(acceptance_context))
    for spec in catalog:
        if spec["name"] in {
            "prepare_dispenser_power",
            "enable_dispenser_output",
            "set_dispenser_current",
        }:
            spec["inputSchema"]["properties"]["action_context"] = (
                action_context_schema()
            )
            spec["inputSchema"].setdefault("required", []).append("action_context")
            spec["description"] += (
                " Requires caller-stated action_context tied to this recording session."
            )
    completion = {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["complete", "incomplete", "aborted", "unknown"],
            },
            "dispenser_response": {
                "type": "string",
                "enum": ["normal", "abnormal", "unknown"],
            },
        },
        "required": ["outcome", "dispenser_response"],
        "additionalProperties": False,
    }
    catalog.append(
        {
            "name": "record_conditioning_decision",
            "description": (
                "Record a caller-stated decision or completion declaration without hardware "
                "action, virtual time advance, or closing/blocking the session."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action_context": action_context_schema(),
                    "completion": completion,
                },
                "required": ["action_context"],
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "action_context": action_context_schema(),
                    "completion": {"anyOf": [completion, {"type": "null"}]},
                    "hardware_action_performed": {"type": "boolean", "const": False},
                },
                "required": [
                    "action_context",
                    "completion",
                    "hardware_action_performed",
                ],
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        }
    )
    return catalog


def _empty_schema(title: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
        "title": title,
    }


def _enable_schema(acceptance_context: str) -> dict[str, Any]:
    if acceptance_context == "production_dispenser":
        field = "parallel_connection_confirmation"
        literal = "confirmed_parallel_ch1"
        description = (
            "Caller attestation after a fresh human physical-state check; "
            "the simulator cannot authenticate its provenance."
        )
    else:
        field = "no_load_test_connection_confirmation"
        literal = "confirmed_no_dispenser_or_unapproved_load_connected"
        description = (
            "Caller attestation after a fresh human physical-state check that "
            "no dispenser or unapproved load is connected. Operator-approved "
            "metrology wiring may remain; the simulator cannot authenticate "
            "the attestation's provenance."
        )
    return {
        "type": "object",
        "properties": {
            field: {
                "type": "string",
                "const": literal,
                "description": description,
            }
        },
        "required": [field],
        "additionalProperties": False,
        "title": "enable_dispenser_outputArguments",
    }


def _pressure_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "observed_at": {"type": "string", "format": "date-time"},
            "pressure_mbar": {"type": "number", "exclusiveMinimum": 0},
            "pressure_torr": {"type": "number", "exclusiveMinimum": 0},
            "source": {
                "type": "string",
                "const": "synthetic.pfeiffer_hicube_neo.pvviewer.g1_total_pressure",
            },
            "p1_drive_serial_number": {"type": "string", "minLength": 1},
            "is_total_gauge_pressure": {"type": "boolean", "const": True},
            "is_rubidium_partial_pressure": {"type": "boolean", "const": False},
            "verifies_dispenser_activation": {"type": "boolean", "const": False},
            "simulated": {"type": "boolean", "const": True},
            "synthetic_provenance": {
                "type": "string",
                "const": "seeded_dynamic_model_not_hardware_evidence",
            },
        },
        "required": [
            "observed_at",
            "pressure_mbar",
            "pressure_torr",
            "source",
            "p1_drive_serial_number",
            "is_total_gauge_pressure",
            "is_rubidium_partial_pressure",
            "verifies_dispenser_activation",
            "simulated",
            "synthetic_provenance",
        ],
        "additionalProperties": False,
        "title": "SyntheticVacuumPressureSnapshot",
    }


def _power_output_schema() -> dict[str, Any]:
    common_trip_properties: dict[str, Any] = {
        "observed_at": {"type": "string", "format": "date-time"},
        "mutating_operation": {"type": "string", "minLength": 1},
    }
    common_trip_required = [
        "observed_at",
        "observed_native_ch1_current_a",
        "reason",
        "mutating_operation",
    ]
    outside_band_trip_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            **common_trip_properties,
            "observed_native_ch1_current_a": {
                "description": (
                    "Signed finite JSON number whose absolute value is greater "
                    "than 0.001 A."
                ),
                "anyOf": [
                    {
                        "type": "number",
                        "exclusiveMaximum": -NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A,
                    },
                    {
                        "type": "number",
                        "exclusiveMinimum": NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A,
                    },
                ],
            },
            "reason": {
                "type": "string",
                "const": "post_operation_measured_native_current_outside_safe_band",
            },
        },
        "required": common_trip_required,
        "additionalProperties": False,
        "title": "NoLoadTestInterlockOutsideSafeBandTrip",
    }
    unavailable_trip_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            **common_trip_properties,
            "observed_native_ch1_current_a": {"type": "null"},
            "reason": {
                "type": "string",
                "const": "post_operation_measured_native_current_unavailable",
            },
        },
        "required": common_trip_required,
        "additionalProperties": False,
        "title": "NoLoadTestInterlockUnavailableTrip",
    }
    trip_record_schema: dict[str, Any] = {
        "oneOf": [
            outside_band_trip_schema,
            unavailable_trip_schema,
        ]
    }
    trip_schema: dict[str, Any] = {
        "anyOf": [
            {"type": "null"},
            trip_record_schema,
        ]
    }
    interlock_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "applicable": {"type": "boolean"},
            "status": {
                "type": "string",
                "enum": [
                    "not_applicable",
                    "unlatched",
                    "latched",
                ],
            },
            "trip": trip_schema,
            "validation_status": {"type": "string"},
        },
        "required": [
            "applicable",
            "status",
            "trip",
            "validation_status",
        ],
        "additionalProperties": False,
        "oneOf": [
            {
                "title": "NoLoadTestInterlockNotApplicable",
                "properties": {
                    "applicable": {"const": False},
                    "status": {"const": "not_applicable"},
                    "trip": {"type": "null"},
                },
            },
            {
                "title": "NoLoadTestInterlockUnlatched",
                "properties": {
                    "applicable": {"const": True},
                    "status": {"const": "unlatched"},
                    "trip": {"type": "null"},
                },
            },
            {
                "title": "NoLoadTestInterlockLatched",
                "properties": {
                    "applicable": {"const": True},
                    "status": {"const": "latched"},
                    "trip": trip_record_schema,
                },
            },
        ],
    }
    properties: dict[str, Any] = {
        "observed_at": {"type": "string", "format": "date-time"},
        "source": {
            "type": "string",
            "const": "synthetic.dispenser_conditioning.power_supply",
        },
        "simulated": {"type": "boolean", "const": True},
        "synthetic_provenance": {
            "type": "string",
            "const": "seeded_dynamic_model_not_hardware_evidence",
        },
        "configured_topology": {"type": "string", "const": "parallel_ch1"},
        "topology_factor": {"type": "integer", "const": 2},
        "native_channel": {"type": "string", "const": "CH1"},
        "expected_operating_mode": {"type": "string", "const": "parallel"},
        "live_operating_mode": {"type": "string"},
        "topology_matches": {"type": "boolean"},
        "manufacturer": {"type": "string"},
        "model": {"type": "string"},
        "serial_number": {"type": "string"},
        "firmware_version": {"type": "string"},
        "native_voltage_setpoint_v": {"type": "number"},
        "native_current_setpoint_a": {"type": "number"},
        "commanded_load_current_limit_a": {"type": "number"},
        "native_voltage_measurement_v": {"type": "number"},
        "native_current_measurement_a": {"type": "number"},
        "native_power_measurement_w": {"type": "number"},
        "output_enabled": {"type": "boolean"},
        "regulation_mode": {"type": "string"},
        "compliance_voltage_matches": {"type": "boolean"},
        "prepared": {"type": "boolean"},
        "safety_limits": {
            "type": "object",
            "properties": {
                "acceptance_context": {"type": "string"},
                "required_enable_confirmation_field": {"type": "string"},
                "required_enable_confirmation_literal": {"type": "string"},
                "fixed_compliance_voltage_v": {"type": "number"},
                "operator_load_current_ceiling_a": {"type": "number"},
                "deployment_load_current_ceiling_a": {"type": "number"},
                "native_current_ceiling_a": {"type": "number"},
                "topology_hardware_load_current_ceiling_a": {"type": "number"},
                "exact_upward_load_current_step_a": {"type": "number"},
                "no_load_test_safe_measured_current_abs_a": {
                    "type": "number",
                    "const": NO_LOAD_TEST_SAFE_MEASURED_CURRENT_ABS_A,
                },
            },
            "required": [
                "acceptance_context",
                "required_enable_confirmation_field",
                "required_enable_confirmation_literal",
                "fixed_compliance_voltage_v",
                "operator_load_current_ceiling_a",
                "deployment_load_current_ceiling_a",
                "native_current_ceiling_a",
                "topology_hardware_load_current_ceiling_a",
                "exact_upward_load_current_step_a",
                "no_load_test_safe_measured_current_abs_a",
            ],
            "additionalProperties": False,
        },
        "driver_hardware_validation_status": {"type": "string"},
        "mcp_read_path_validation_status": {"type": "string"},
        "mcp_actuation_validation_status": {"type": "string"},
        "safety_state": {
            "type": "object",
            "properties": {
                "simulator_guard_latched": {"type": "boolean"},
                "last_transition": {"type": "string"},
                "guard_is_production_contract_feature": {
                    "type": "boolean",
                    "const": False,
                },
            },
            "required": [
                "simulator_guard_latched",
                "last_transition",
                "guard_is_production_contract_feature",
            ],
            "additionalProperties": False,
        },
        "active_faults": {"type": "array", "items": {"type": "string"}},
        "no_load_test_interlock": interlock_schema,
        "verifies_dispenser_activation": {"type": "boolean", "const": False},
        "wrote_hardware": {"type": "boolean", "const": False},
        "write_was_synthetic": {"type": "boolean", "const": True},
        "simulator_state_mutated": {"type": "boolean"},
    }
    required = [
        name
        for name in properties
        if name
        not in {"wrote_hardware", "write_was_synthetic", "simulator_state_mutated"}
    ]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "title": "SyntheticDispenserPowerState",
    }


def tool_specs(acceptance_context: str) -> list[dict[str, Any]]:
    """Return the six production-named tools with closed startup-bound schemas."""

    if acceptance_context not in {"production_dispenser", "no_load_test"}:
        raise ValueError("Unsupported acceptance context")

    specs: list[dict[str, Any]] = [
        {
            "name": "read_dispenser_power_state",
            "description": (
                "Read one synthetic configured-channel power snapshot. "
                "No hardware, gateway, or live endpoint is accessed."
            ),
            "inputSchema": _empty_schema("read_dispenser_power_stateArguments"),
            "outputSchema": _power_output_schema(),
            "annotations": READ_ANNOTATIONS,
        },
        {
            "name": "prepare_dispenser_power",
            "description": (
                "Apply the synthetic off, zero-current, fixed-compliance prepare sequence."
            ),
            "inputSchema": _empty_schema("prepare_dispenser_powerArguments"),
            "outputSchema": _power_output_schema(),
            "annotations": MUTATING_IDEMPOTENT_ANNOTATIONS,
        },
        {
            "name": "enable_dispenser_output",
            "description": (
                "Enable the synthetic output at zero current after the startup-context "
                "caller attestation. The attestation is not independently authenticated."
            ),
            "inputSchema": _enable_schema(acceptance_context),
            "outputSchema": _power_output_schema(),
            "annotations": MUTATING_NONIDEMPOTENT_ANNOTATIONS,
        },
        {
            "name": "set_dispenser_current",
            "description": (
                "Compare-and-set the synthetic absolute load-current limit. Exact "
                "0.2 A increases and arbitrary safe decreases are accepted."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_current_a": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 4.8,
                    },
                    "expected_current_a": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 6.4,
                    },
                },
                "required": ["target_current_a", "expected_current_a"],
                "additionalProperties": False,
                "title": "set_dispenser_currentArguments",
            },
            "outputSchema": _power_output_schema(),
            "annotations": MUTATING_NONIDEMPOTENT_ANNOTATIONS,
        },
        {
            "name": "shutdown_dispenser_power",
            "description": (
                "Apply the synthetic two-output off and two-current-zero shutdown. "
                "This is not a physical emergency stop."
            ),
            "inputSchema": _empty_schema("shutdown_dispenser_powerArguments"),
            "outputSchema": _power_output_schema(),
            "annotations": MUTATING_IDEMPOTENT_ANNOTATIONS,
        },
        {
            "name": "read_vacuum_pressure",
            "description": (
                "Read one synthetic G1 total-pressure snapshot; never infer dispenser "
                "activation or rubidium partial pressure."
            ),
            "inputSchema": _empty_schema("read_vacuum_pressureArguments"),
            "outputSchema": _pressure_output_schema(),
            "annotations": READ_ANNOTATIONS,
        },
    ]
    for spec in specs:
        if spec["name"] != "shutdown_dispenser_power":
            spec["inputSchema"]["properties"]["elapsed_s"] = {
                "type": "number",
                "minimum": 0,
                "maximum": 86400,
                "default": 0,
                "description": "Simulated elapsed seconds since the previous physical interaction; actual monotonic wall elapsed is the minimum. Evolves prior output state before this action.",
            }
        spec["description"] += (
            " Simulation time advances irreversibly by max(requested elapsed_s, actual wall elapsed); no intermediate instrument samples are fabricated."
        )
        spec["outputSchema"]["properties"]["timing"] = {
            "type": "object",
            "properties": {
                key: {"type": "number", "minimum": 0}
                for key in (
                    "requested_elapsed_s",
                    "wall_elapsed_s",
                    "advanced_s",
                    "virtual_time_s",
                )
            },
            "required": [
                "requested_elapsed_s",
                "wall_elapsed_s",
                "advanced_s",
                "virtual_time_s",
            ],
            "additionalProperties": False,
        }
        spec["outputSchema"]["required"].append("timing")
    return specs
