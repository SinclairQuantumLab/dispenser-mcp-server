"""CLI-only best-effort connection reads, without instrument mutations."""

import sys
import traceback
from collections.abc import Callable

from dispenser_conditioning_mcp.domain import (
    PressureObservationSource,
    normalize_observation,
)
from dispenser_conditioning_mcp.power_domain import PowerController


def _failure(error: BaseException) -> str:
    """Expose causal classes/locations, never messages or traceback local values."""
    parts: list[str] = []
    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        frames = traceback.extract_tb(cause.__traceback__)
        location = f" at {frames[-1].name}:{frames[-1].lineno}" if frames else ""
        parts.append(type(cause).__name__ + location)
        cause = cause.__cause__ or cause.__context__
    return " <- ".join(parts)


def check_connections(
    pressure: PressureObservationSource, power: PowerController
) -> None:
    def g1() -> str:
        reading = normalize_observation(pressure.read())
        return f"G1={reading.pressure_mbar:.6g} mbar"

    def psu() -> str:
        state = power.read_state()
        return (
            f"{state.manufacturer} {state.model} serial={state.serial_number} "
            f"output={'ON' if state.output_enabled else 'OFF'} "
            f"load setting={state.commanded_load_current_limit_a:g} A "
            f"native CH1 measured={state.measured_native_channel_current_a:g} A"
        )

    checks: tuple[tuple[str, Callable[[], str]], ...] = (("G1", g1), ("PSU", psu))
    for name, read in checks:
        try:
            print(f"Startup read PASS [{name}] {read()}", file=sys.stderr)
        except Exception as error:
            print(
                f"Startup read FAIL [{name}] {_failure(error)}; continuing HTTP startup",
                file=sys.stderr,
            )
