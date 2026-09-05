"""Server-owned append-only session records and reusable CSV projections."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

CONTROL_TOOLS = {
    "prepare_dispenser_power",
    "enable_dispenser_output",
    "set_dispenser_current",
    "shutdown_dispenser_power",
}
COMMON = [
    "event_id",
    "session_id",
    "recorded_at",
    "received_at",
    "decision_at",
    "observed_at",
    "virtual_time_s",
    "virtual_time_basis",
    "source",
    "call_id",
    "decision_id",
    "tool",
]
HEADERS = {
    "observations": COMMON
    + [
        "observation_kind",
        "pressure_mbar",
        "pressure_torr",
        "commanded_load_current_limit_a",
        "native_ch1_current_setpoint_a",
        "native_ch1_measured_current_a",
        "native_ch1_voltage_setpoint_v",
        "native_ch1_measured_voltage_v",
        "native_ch1_measured_power_w",
        "output_enabled",
        "fixed_compliance_voltage_v",
        "model",
        "serial_number",
        "measurement_source",
        "simulated",
    ],
    "controls": COMMON
    + [
        "phase",
        "status",
        "requested_load_current_a",
        "expected_load_current_a",
        "confirmed_load_current_limit_a",
        "native_ch1_measured_current_a",
        "output_enabled",
        "error",
        "arguments_json",
    ],
    "decisions": COMMON
    + [
        "chosen_action",
        "rationale_summary",
        "basis_event_ids_json",
        "background",
        "confidence_claim",
        "confidence_value",
        "completion_outcome",
        "dispenser_response",
    ],
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    return json.loads(json.dumps(value, allow_nan=False))


def as_dict(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def content_state(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("structuredContent", result.get("structured_content"))
    if not isinstance(content, dict):
        return {}
    content = as_dict(content)
    return as_dict(content.get("state", content))


def result_failed(result: dict[str, Any]) -> bool:
    return result.get("isError", result.get("is_error", False)) is True


def error_text(result: dict[str, Any]) -> str:
    return "\n".join(
        str(as_dict(part).get("text", ""))
        for part in result.get("content", [])
        if isinstance(part, dict) and as_dict(part).get("type") == "text"
    )


def number(value: Any) -> float | int | None:
    return value if type(value) in (int, float) and math.isfinite(value) else None


def first(state: dict[str, Any], *names: str) -> Any:
    return next((state[name] for name in names if state.get(name) is not None), None)


def projections(event: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Pure CSV projection of one canonical event; no observer truth input."""
    payload = event["payload"]
    kind = event["kind"]
    tool = payload.get("tool")
    result = as_dict(payload.get("result"))
    state = content_state(result)
    base = {key: event.get(key) for key in COMMON}
    base.update(tool=tool, observed_at=state.get("observed_at"))
    rows: list[tuple[str, dict[str, Any]]] = []
    measured_a = number(
        first(
            state, "measured_native_channel_current_a", "native_current_measurement_a"
        )
    )
    if kind == "decision":
        rows.append(
            (
                "decisions",
                {
                    **base,
                    "chosen_action": payload["chosen_action"],
                    "rationale_summary": payload["rationale_summary"],
                    "basis_event_ids_json": json.dumps(payload["basis_event_ids"]),
                    "background": as_dict(payload.get("action_context")).get(
                        "background"
                    ),
                    "confidence_claim": as_dict(
                        as_dict(payload.get("action_context")).get("confidence")
                    ).get("claim"),
                    "confidence_value": as_dict(
                        as_dict(payload.get("action_context")).get("confidence")
                    ).get("value"),
                    "completion_outcome": as_dict(payload.get("completion")).get(
                        "outcome"
                    ),
                    "dispenser_response": as_dict(payload.get("completion")).get(
                        "dispenser_response"
                    ),
                },
            )
        )
    if tool in CONTROL_TOOLS:
        args = as_dict(payload.get("arguments"))
        status = (
            "intent"
            if kind == "call_intent"
            else (
                "failed"
                if kind == "call_error" or result_failed(result)
                else "succeeded"
            )
        )
        rows.append(
            (
                "controls",
                {
                    **base,
                    "phase": kind,
                    "status": status,
                    "requested_load_current_a": number(args.get("target_current_a")),
                    "expected_load_current_a": number(args.get("expected_current_a")),
                    "confirmed_load_current_limit_a": number(
                        state.get("commanded_load_current_limit_a")
                    )
                    if status == "succeeded"
                    else None,
                    "native_ch1_measured_current_a": measured_a,
                    "output_enabled": state.get("output_enabled"),
                    "error": payload.get("error_message")
                    or (error_text(result) if result_failed(result) else None),
                    "arguments_json": json.dumps(args, ensure_ascii=False),
                },
            )
        )
    if kind == "call_result" and not result_failed(result) and state:
        observation_kind = (
            "pressure"
            if "pressure_mbar" in state
            else ("power" if "native_current_setpoint_a" in state else None)
        )
        if observation_kind:
            limits = as_dict(state.get("safety_limits"))
            rows.append(
                (
                    "observations",
                    {
                        **base,
                        "observation_kind": observation_kind,
                        "pressure_mbar": number(state.get("pressure_mbar")),
                        "pressure_torr": number(state.get("pressure_torr")),
                        "commanded_load_current_limit_a": number(
                            state.get("commanded_load_current_limit_a")
                        ),
                        "native_ch1_current_setpoint_a": number(
                            state.get("native_current_setpoint_a")
                        ),
                        "native_ch1_measured_current_a": measured_a,
                        "native_ch1_voltage_setpoint_v": number(
                            state.get("native_voltage_setpoint_v")
                        ),
                        "native_ch1_measured_voltage_v": number(
                            first(
                                state,
                                "measured_native_channel_voltage_v",
                                "native_voltage_measurement_v",
                            )
                        ),
                        "native_ch1_measured_power_w": number(
                            first(
                                state,
                                "measured_native_channel_power_w",
                                "native_power_measurement_w",
                            )
                        ),
                        "output_enabled": state.get("output_enabled"),
                        "fixed_compliance_voltage_v": number(
                            limits.get("fixed_compliance_voltage_v")
                        ),
                        "model": state.get("model"),
                        "serial_number": first(
                            state, "serial_number", "p1_drive_serial_number"
                        ),
                        "measurement_source": state.get("source"),
                        "simulated": state.get("simulated"),
                    },
                )
            )
    return rows


class RecordingError(RuntimeError):
    """Visible recording failure; a completed MCP call must never be retried blindly."""


class PostCallRecordingError(RecordingError):
    """The MCP returned; inspect result instead of replaying the call for logging."""

    def __init__(self, result: Any, call_id: str, error: Exception) -> None:
        self.result = result
        self.call_id = call_id
        super().__init__(
            f"MCP call {call_id} returned, but recording failed: {error}. "
            "The original result is available as error.result. Do not retry the call to repair logs."
        )


class TransportRecordingError(RecordingError):
    """Both the chosen call and its error recording failed; neither is hidden."""

    def __init__(
        self, call_id: str, transport_error: Exception, recording_error: Exception
    ) -> None:
        self.call_id = call_id
        self.transport_error = transport_error
        self.recording_error = recording_error
        super().__init__(
            f"Call {call_id} raised {type(transport_error).__name__}: {transport_error}; "
            f"recording that failure also failed: {recording_error}. Device outcome may be unknown. "
            "No retry was performed."
        )


@dataclass(frozen=True)
class RecordedCall:
    result: Any
    call_id: str
    intent_event_id: str
    result_event_id: str


class SessionRecorder:
    """One writer per new session directory; not a resume or orchestration service."""

    def __init__(
        self,
        directory: Path | str,
        *,
        source: str,
        session_kind: str,
        label: str,
        observed_time_origin: str | None = None,
    ) -> None:
        if source not in {"agent", "scripted", "human"}:
            raise ValueError("source must be agent, scripted, or human")
        if session_kind not in {"live", "simulated", "format_fixture"}:
            raise ValueError("session_kind must be live, simulated, or format_fixture")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        if any(self.directory.iterdir()):
            raise ValueError(
                "Use a new empty session directory; existing sessions are preserved"
            )
        if observed_time_origin:
            origin = datetime.fromisoformat(observed_time_origin.replace("Z", "+00:00"))
            if origin.tzinfo is None:
                raise ValueError("observed_time_origin must include its UTC offset")
        self.source = source
        self.session_id = str(uuid4())
        self.observed_time_origin = observed_time_origin
        self.metadata = {
            "schema_version": 1,
            "session_id": self.session_id,
            "created_at": utc_now(),
            "source": source,
            "session_kind": session_kind,
            "label": label,
            "observed_time_origin": observed_time_origin,
        }
        (self.directory / "metadata.json").write_text(
            json.dumps(self.metadata, indent=2) + "\n", encoding="utf-8"
        )
        (self.directory / "events.jsonl").touch(exist_ok=False)
        for name, headers in HEADERS.items():
            with (self.directory / f"{name}.csv").open(
                "w", newline="", encoding="utf-8"
            ) as stream:
                csv.DictWriter(stream, headers).writeheader()

    def append_event(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        call_id: str | None = None,
        decision_id: str | None = None,
        virtual_time_s: float | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "session_id": self.session_id,
            "recorded_at": utc_now(),
            "received_at": payload.get("received_at"),
            "decision_at": as_dict(payload.get("action_context")).get("decision_at"),
            "kind": kind,
            "source": self.source,
            "call_id": call_id,
            "decision_id": decision_id,
            "virtual_time_s": number(virtual_time_s),
            "virtual_time_basis": "caller_context"
            if number(virtual_time_s) is not None
            else None,
            "payload": json_value(payload),
        }
        state = content_state(as_dict(payload.get("result")))
        observed_at = state.get("observed_at")
        timestamp = observed_at or event.get("decision_at")
        if timestamp and self.observed_time_origin:
            try:
                observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                origin = datetime.fromisoformat(
                    self.observed_time_origin.replace("Z", "+00:00")
                )
                event["virtual_time_s"] = (observed - origin).total_seconds()
                event["virtual_time_basis"] = (
                    "observed_time_origin" if observed_at else "agent_decision_time"
                )
            except (ValueError, TypeError, AttributeError):
                event["virtual_time_s"] = None
                event["virtual_time_basis"] = None
        try:
            with (self.directory / "events.jsonl").open(
                "a", encoding="utf-8", newline="\n"
            ) as stream:
                stream.write(
                    json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
                )
                stream.flush()
        except OSError as error:
            raise RecordingError(
                f"Raw event write failed ({kind}, call {call_id}): {error}"
            ) from error
        try:
            for name, row in projections(event):
                with (self.directory / f"{name}.csv").open(
                    "a", newline="", encoding="utf-8"
                ) as stream:
                    csv.DictWriter(stream, HEADERS[name]).writerow(row)
        except OSError as error:
            raise RecordingError(
                f"Raw event {event['event_id']} is saved, but CSV projection failed: {error}. "
                "Rebuild CSV from raw events; do not repeat the MCP call to repair logs."
            ) from error
        return event

    def record_decision(
        self,
        chosen_action: str,
        rationale_summary: str,
        basis_event_ids: list[str],
        *,
        virtual_time_s: float | None = None,
    ) -> dict[str, Any]:
        """Record the caller's concise stated reason, never hidden chain-of-thought."""
        return self.append_event(
            "decision",
            {
                "chosen_action": chosen_action,
                "rationale_summary": rationale_summary,
                "basis_event_ids": basis_event_ids,
            },
            decision_id=str(uuid4()),
            virtual_time_s=virtual_time_s,
        )

    def record_call_intent(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        decision_id: str | None = None,
        virtual_time_s: float | None = None,
    ) -> dict[str, Any]:
        """For external calls: invoke this before dispatching the chosen call."""
        return self.append_event(
            "call_intent",
            {"tool": name, "arguments": json_value(arguments or {})},
            call_id=str(uuid4()),
            decision_id=decision_id,
            virtual_time_s=virtual_time_s,
        )

    def record_call_result(self, intent: dict[str, Any], result: Any) -> dict[str, Any]:
        """Record an already-returned result, retaining its original object on failure."""
        try:
            return self.append_event(
                "call_result",
                {**intent["payload"], "result": json_value(result)},
                call_id=intent["call_id"],
                decision_id=intent["decision_id"],
                virtual_time_s=intent.get("virtual_time_s"),
            )
        except Exception as error:
            raise PostCallRecordingError(result, intent["call_id"], error) from error

    def record_call_error(
        self, intent: dict[str, Any], error: Exception
    ) -> dict[str, Any]:
        """Record a transport/client exception; device outcome may be unknown."""
        return self.append_event(
            "call_error",
            {
                **intent["payload"],
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
            call_id=intent["call_id"],
            decision_id=intent["decision_id"],
            virtual_time_s=intent.get("virtual_time_s"),
        )

    async def call_andappend_event(
        self,
        client: Any,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        decision_id: str | None = None,
        virtual_time_s: float | None = None,
    ) -> RecordedCall:
        """Invoke exactly the caller-selected call once, recording intent first."""
        intent = self.record_call_intent(
            name, arguments, decision_id=decision_id, virtual_time_s=virtual_time_s
        )
        try:
            result = await client.call_tool(name, intent["payload"]["arguments"])
        except Exception as error:
            try:
                self.record_call_error(intent, error)
            except Exception as recording_error:  # noqa: BLE001 - preserve both failures
                raise TransportRecordingError(
                    intent["call_id"], error, recording_error
                ) from error
            raise
        saved = self.record_call_result(intent, result)
        return RecordedCall(
            result, intent["call_id"], intent["event_id"], saved["event_id"]
        )


def rebuild(directory: Path) -> int:
    """Regenerate CSVs from complete canonical records while the writer is stopped."""
    events: list[dict[str, Any]] = []
    with (directory / "events.jsonl").open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.endswith(b"\n"):
                raise ValueError(
                    f"Incomplete raw event at line {line_number}; preserve and inspect it"
                )
            events.append(json.loads(line))
    # Parse and project everything first, so bad input cannot erase prior CSVs.
    projected: dict[str, list[dict[str, Any]]] = {name: [] for name in HEADERS}
    for event in events:
        for name, row in projections(event):
            projected[name].append(row)
    for name, headers in HEADERS.items():
        with (directory / f"{name}.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.DictWriter(stream, headers)
            writer.writeheader()
            writer.writerows(projected[name])
    return len(events)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["rebuild"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(f"Rebuilt CSV projections from {rebuild(args.directory)} raw events.")
