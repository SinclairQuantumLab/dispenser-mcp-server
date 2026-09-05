"""Shared source-checkout location for independent conditioning run records."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

RUNS_DIRECTORY = Path(__file__).resolve().parents[2] / "runs"


def new_run_directory(mode: Literal["live", "simulation"]) -> Path:
    """Choose a fresh path; SessionRecorder creates it without overwriting data."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return RUNS_DIRECTORY / f"{timestamp}_{mode}_{uuid4().hex[:8]}"
