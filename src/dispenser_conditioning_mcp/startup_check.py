"""CLI-only fail-fast connection reads, without instrument mutations."""

import sys
from collections.abc import Callable

from dispenser_conditioning_mcp.domain import (
    PressureObservationSource,
    normalize_observation,
)
from dispenser_conditioning_mcp.power_domain import PowerController


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
        except Exception:
            print(
                f"Startup read FAIL [{name}]; aborting HTTP startup",
                file=sys.stderr,
            )
            raise
