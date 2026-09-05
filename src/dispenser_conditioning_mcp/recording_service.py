"""MCP-owned context and recording boundary, independent of instrument drivers."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from mcp.types import CallToolResult, TextContent
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictFloat,
    ValidationError,
)

from dispenser_conditioning_mcp.run_directory import new_run_directory
from dispenser_conditioning_mcp.session_records import (
    SessionRecorder,
    content_state,
    utc_now,
)

LOGGER = logging.getLogger(__name__)
DECLARATION_TOOL = "record_conditioning_decision"
NORMAL_CONTROLS = {
    "prepare_dispenser_power",
    "enable_dispenser_output",
    "set_dispenser_current",
}
SHUTDOWN_TOOL = "shutdown_dispenser_power"
_STARTED: ContextVar[list[bool] | None] = ContextVar(
    "instrument_dispatch_started", default=None
)


def mark_execution_started() -> None:
    """Adapter calls this only after tool argument validation, before domain access."""
    state = _STARTED.get()
    if state is not None:
        state[0] = True


def _decision_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("decision_at must be an ISO8601 string with timezone")
    return value


class ClaimConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    claim: str = Field(min_length=1, max_length=1000)
    value: Annotated[StrictFloat, Field(ge=0, le=1)] | None


class ActionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(min_length=1, max_length=128)
    decision_at: Annotated[AwareDatetime, BeforeValidator(_decision_timestamp)]
    action: str = Field(min_length=1, max_length=500)
    background: str = Field(min_length=1, max_length=4000)
    rationale_summary: str = Field(min_length=1, max_length=2000)
    observation_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        max_length=100
    )
    confidence: ClaimConfidence


class CompletionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["complete", "incomplete", "aborted", "unknown"]
    dispenser_response: Literal["normal", "abnormal", "unknown"]


class DeclarationResult(BaseModel):
    action_context: ActionContext
    completion: CompletionAssessment | None
    hardware_action_performed: Literal[False] = False


def error_result(message: str) -> CallToolResult:
    return CallToolResult(
        is_error=True, content=[TextContent(type="text", text=message)]
    )


class RecordingService:
    """One process-scoped session; no polling, actuation selection or retry logic."""

    def __init__(
        self, recorder: SessionRecorder | None = None, *, directory: Path | None = None
    ) -> None:
        self.directory = directory or new_run_directory("live")
        self.session_id = str(uuid4())
        self.recorder = recorder
        self.observation_ids: set[str] = set()
        if recorder is None:
            try:
                self.recorder = SessionRecorder(
                    self.directory,
                    source="agent",
                    session_kind="live",
                    label="Conditioning MCP session",
                )
            except OSError:
                LOGGER.exception(
                    "Session recording unavailable; shutdown remains available under existing controller policy"
                )
        if self.recorder is not None:
            self.directory = self.recorder.directory
            self.session_id = self.recorder.session_id

    def _append(
        self, kind: str, payload: dict[str, Any], call_id: str, decision_id: str | None
    ) -> dict[str, Any]:
        if self.recorder is None:
            raise OSError("Session recorder unavailable")
        return self.recorder.append_event(
            kind, payload, call_id=call_id, decision_id=decision_id
        )

    async def process_call(
        self,
        name: str,
        arguments: dict[str, Any],
        dispatch: Callable[[str, dict[str, Any]], Awaitable[CallToolResult]],
        *,
        rejection: str | None = None,
    ) -> CallToolResult:
        """Validate context, record and dispatch once with action_context removed.

        Declaration never dispatches. Shutdown dispatches before recorder access.
        The instrument adapter marks execution start after its argument validation;
        a simulator adapter may do so immediately before its public router call.
        """
        call_id, received_at = str(uuid4()), utc_now()
        decision_id: str | None = None
        record: dict[str, Any] | None = None
        warnings: list[str] = []
        context: ActionContext | None = None
        completion: CompletionAssessment | None = None
        started = [
            False
        ]  # Shared across the SDK's sync-tool worker-thread context copy.
        token = _STARTED.set(started)
        request = {"tool": name, "arguments": arguments, "received_at": received_at}
        try:
            if rejection is None and name in NORMAL_CONTROLS | {DECLARATION_TOOL}:
                try:
                    context = ActionContext.model_validate(
                        arguments.get("action_context")
                    )
                    if context.session_id != self.session_id:
                        raise ValueError(
                            "action_context.session_id does not match this session"
                        )
                    if any(
                        item not in self.observation_ids
                        for item in context.observation_ids
                    ):
                        raise ValueError(
                            "action_context references unknown session observations"
                        )
                    if arguments.get("completion") is not None:
                        completion = CompletionAssessment.model_validate(
                            arguments["completion"]
                        )
                except (ValidationError, ValueError):
                    context = None
                    rejection = "Not executed: action_context is missing/invalid, its session or observation references are unknown, or completion is invalid."
            if context is not None:
                request["action_context"] = context.model_dump(mode="json")
                decision_id = str(uuid4())

            def append(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
                try:
                    return self._append(kind, payload, call_id, decision_id)
                except Exception:
                    LOGGER.exception(
                        "Session recording failed for call %s (%s)", call_id, kind
                    )
                    warnings.append(
                        "Session recording is incomplete; inspect operator logs. Do not repeat a completed action to repair records."
                    )
                    return None

            if name != SHUTDOWN_TOOL:
                if context is not None and rejection is None:
                    decision = append(
                        "decision",
                        {
                            "chosen_action": context.action,
                            "rationale_summary": context.rationale_summary,
                            "basis_event_ids": context.observation_ids,
                            "action_context": context.model_dump(mode="json"),
                            "completion": completion.model_dump()
                            if completion
                            else None,
                            "received_at": received_at,
                        },
                    )
                    if decision is None:
                        rejection = (
                            "Not executed: decision recording failed before dispatch."
                        )
                intent = append("call_intent", request)
                if intent is None and name in NORMAL_CONTROLS | {DECLARATION_TOOL}:
                    rejection = (
                        "Not executed: session recording failed before dispatch."
                    )

            if rejection is not None:
                result = error_result(rejection)
            elif name == DECLARATION_TOOL:
                assert context is not None
                declaration = DeclarationResult(
                    action_context=context, completion=completion
                )
                result = CallToolResult(
                    content=[
                        TextContent(type="text", text=declaration.model_dump_json())
                    ],
                    structured_content=declaration.model_dump(mode="json"),
                )
            else:
                clean_arguments = {
                    key: value
                    for key, value in arguments.items()
                    if key != "action_context"
                }
                try:
                    result = await dispatch(name, clean_arguments)
                except Exception:
                    LOGGER.exception("Tool dispatch failed for %s", name)
                    result = error_result(
                        "Tool execution failed. Consult operator diagnostics; do not blindly repeat a control call."
                    )

            execution = (
                "not_executed"
                if rejection
                else (
                    "completed"
                    if not result.is_error
                    else ("failed_or_unknown" if started[0] else "not_executed")
                )
            )
            # Shutdown has no ordinary disk access before its domain operation.
            if name == SHUTDOWN_TOOL:
                append(
                    "call_intent", {**request, "intent_recorded_after_dispatch": True}
                )
            record = append(
                "call_result",
                {
                    **request,
                    "result": result.model_dump(mode="json", by_alias=True),
                    "execution": execution,
                },
            )
            observation_id = None
            state = content_state(result.model_dump(mode="json", by_alias=True))
            if (
                not result.is_error
                and record
                and ("pressure_mbar" in state or "native_current_setpoint_a" in state)
            ):
                observation_id = str(record["event_id"])
                self.observation_ids.add(observation_id)
            metadata = {
                "session_id": self.session_id,
                "call_id": call_id,
                "event_id": record["event_id"] if record else None,
                "observation_id": observation_id,
                "decision_id": decision_id,
                "received_at": received_at,
                "recorded_at": record["recorded_at"] if record else None,
                "recording_status": "degraded" if warnings else "recorded",
                "execution": execution,
                "warning": warnings[0] if warnings else None,
            }
            summary = f"Session {self.session_id}; call {call_id}; observation {observation_id or 'none'}; execution {execution}; recording {metadata['recording_status']}."
            if warnings:
                summary += " " + warnings[0]
            return result.model_copy(
                update={
                    "meta": {**(result.meta or {}), "dispenser_conditioning": metadata},
                    "content": [
                        *result.content,
                        TextContent(type="text", text=summary),
                    ],
                }
            )
        finally:
            _STARTED.reset(token)
