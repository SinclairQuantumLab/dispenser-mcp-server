"""Opt-in caller-side usage checkpoints. Never imported by the MCP server.

Read only an explicitly selected Codex rollout. One caller owns each cursor.
Pending batches repeat until acknowledge() after confirmed MCP recording.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

COUNTS = ("total_tokens", "input_tokens", "cached_input_tokens", "output_tokens")


class UsageUnavailable(ValueError):
    """No trustworthy new usage checkpoint; omit metrics, not the MCP action."""


def _scan(path: Path, offset: int) -> tuple[int, dict[str, int] | None]:
    latest = None
    invalid = False
    with path.open("rb") as stream:
        if stream.seek(0, 2) < offset:
            raise UsageUnavailable(
                "Rollout was truncated; start a new explicit baseline"
            )
        stream.seek(offset)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                raise UsageUnavailable(
                    "Rollout has an incomplete final line; try a later checkpoint"
                )
            offset = stream.tell()
            try:
                row = json.loads(line)
                if row.get("type") != "event_msg":
                    continue
                payload = row.get("payload", {})
                if payload.get("type") != "token_count":
                    continue
                counts = (payload.get("info") or {}).get("total_token_usage", {})
                if not all(
                    type(counts.get(k)) is int and counts[k] >= 0 for k in COUNTS
                ):
                    raise ValueError("Incomplete usage counters")
                latest = {k: counts[k] for k in COUNTS}
                invalid = False
            except (ValueError, AttributeError, TypeError):
                invalid = True
    if invalid:
        raise UsageUnavailable(
            "Latest complete usage metadata is malformed/unavailable"
        )
    return offset, latest


class CodexUsageCheckpoint:
    def __init__(self, rollout: Path, cursor: Path):
        self.rollout = rollout.resolve(strict=True)
        self.cursor = cursor

    def _save(self, state: dict[str, Any]) -> None:
        temporary = self.cursor.with_suffix(self.cursor.suffix + ".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self.cursor)

    def _load(self) -> dict[str, Any]:
        state = json.loads(self.cursor.read_text(encoding="utf-8"))
        if state["rollout"] != str(self.rollout):
            raise UsageUnavailable(
                "Cursor belongs to a different explicitly selected rollout"
            )
        return state

    def baseline(self) -> None:
        """Explicitly exclude all prior thread usage; never overwrite a run cursor."""
        if self.cursor.exists():
            raise FileExistsError("Use a new cursor for a new conditioning run")
        offset, counts = _scan(self.rollout, 0)
        if counts is None:
            raise UsageUnavailable("No usage metadata available for baseline")
        self._save(
            {
                "rollout": str(self.rollout),
                "offset": offset,
                "counts": counts,
                "pending": None,
            }
        )

    def decorate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Copy existing action arguments and attach a pending checkpoint delta.

        No counts yet returns unchanged arguments. Errors remain visible to the
        caller, who may send the original action without optional usage. Never
        retry a hardware command merely to repair usage reporting.
        """
        decorated = deepcopy(arguments)
        context = decorated.get("action_context")
        if not isinstance(context, dict):
            raise ValueError(
                "Usage requires an existing action_context; shutdown needs none"
            )
        if context.get("token_usage") is not None:
            raise ValueError("Do not overwrite an existing token_usage report")
        state = self._load()
        pending = state["pending"]
        if pending is None:
            offset, counts = _scan(self.rollout, state["offset"])
            if counts is None:
                return decorated
            delta = {k: counts[k] - state["counts"][k] for k in COUNTS}
            if any(value < 0 for value in delta.values()):
                raise UsageUnavailable(
                    "Usage counters reset; no negative usage will be reported"
                )
            if not any(delta.values()):
                return decorated  # Repeated accounting event, not a zero-cost action.
            pending = {
                "usage": {"usage_id": str(uuid4()), **delta},
                "offset": offset,
                "counts": counts,
            }
            state["pending"] = pending
            self._save(state)
        context["token_usage"] = pending["usage"]
        return decorated

    def acknowledge(self, usage_id: str) -> None:
        """Call only after the carrier reports recording_status='recorded'."""
        state = self._load()
        pending = state["pending"]
        if pending is None or pending["usage"]["usage_id"] != usage_id:
            raise ValueError("Acknowledgement does not match the pending usage batch")
        state.update(offset=pending["offset"], counts=pending["counts"], pending=None)
        self._save(state)
