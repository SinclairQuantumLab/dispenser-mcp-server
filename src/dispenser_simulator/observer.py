"""Append-only human observer data, separate from every MCP result."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4


class Observer:
    def __init__(self, path: str | None):
        self.path = Path(path) if path is not None else None
        if self.path is not None and not self.path.is_absolute():
            raise ValueError("Observer path must be absolute")
        self.run_id = str(uuid4())
        self.sequence = 0
        self.error_count = 0
        self._last_error: str | None = None

    def append(self, snapshot: dict[str, Any]) -> None:
        if self.path is None:
            return
        record = {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "observer_error_count": self.error_count,
            **snapshot,
        }
        self.sequence += 1
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, allow_nan=False) + "\n")
        except (OSError, ValueError) as exc:
            # An optional display failure must not change actuation semantics.
            self.error_count += 1
            reason = f"{type(exc).__name__}: {exc}"
            if reason != self._last_error:
                print(
                    f"Simulator observer append failed at {self.path}: {reason}",
                    file=sys.stderr,
                )
                self._last_error = reason
