"""Public result of applying the operator's current cap without actuation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RELOAD_CURRENT_LIMIT_TOOL = "reload_dispenser_current_limit"


class CurrentLimitReloadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_max_load_current_A: float = Field(gt=0, le=4.8)
    applied_max_load_current_A: float = Field(gt=0, le=4.8)
    effective_max_load_current_A: float = Field(gt=0, le=4.8)
    hardware_changed: Literal[False] = False
    fresh_state_inspection_recommended: bool
    notice: str


def reload_result(previous: float, applied: float) -> CurrentLimitReloadResult:
    return CurrentLimitReloadResult(
        previous_max_load_current_A=previous,
        applied_max_load_current_A=applied,
        effective_max_load_current_A=min(applied, 4.8),
        fresh_state_inspection_recommended=applied < previous,
        notice="Only the operator current cap was applied. Output/current were not queried or changed. Inspect fresh state after lowering; existing output may exceed the new cap. Subsequent targets must be within the cap; shutdown remains available.",
    )
