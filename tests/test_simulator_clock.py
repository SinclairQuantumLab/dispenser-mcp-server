"""Clock regressions with synthetic inputs and no wall sleeps or hardware."""

from collections.abc import Callable

import pytest

from dispenser_simulator.model import (
    HiddenSimulatorConfig,
    SimulatedDispenser,
    SimulationError,
    ToolRouter,
)


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(clock: Callable[[], float]) -> SimulatedDispenser:
    return SimulatedDispenser(
        HiddenSimulatorConfig(seed="clock-test", scenario="nominal_recovery"),
        monotonic=clock,
    )


def test_first_anchor_wall_floor_requested_interval_and_observer() -> None:
    clock = Clock()
    sim = make(clock)
    clock.now += 500
    assert sim.read_vacuum_pressure()["timing"] == {
        "requested_elapsed_s": 0.0,
        "wall_elapsed_s": 0.0,
        "advanced_s": 0.0,
        "virtual_time_s": 0.0,
    }
    clock.now += 11
    sim.observe("call")
    clock.now += 9
    result = sim.read_dispenser_power_state(elapsed_s=3)
    assert result["timing"]["advanced_s"] == 20
    clock.now += 2
    result = sim.read_vacuum_pressure(elapsed_s=75)
    assert result["timing"] == {
        "requested_elapsed_s": 75.0,
        "wall_elapsed_s": 2.0,
        "advanced_s": 75.0,
        "virtual_time_s": 95.0,
    }
    sim.read_vacuum_pressure()
    assert sim.timing["advanced_s"] == 0
    assert sim.state.virtual_time_s == 95


@pytest.mark.parametrize(
    "value", [-1, float("nan"), float("inf"), True, "1", None, 86401, 10**400]
)
def test_invalid_interval_preserves_time_and_anchor(value: object) -> None:
    clock = Clock()
    sim = make(clock)
    router = ToolRouter(sim)
    sim.read_vacuum_pressure()
    clock.now += 7
    with pytest.raises(SimulationError, match="elapsed_s"):
        router.call("prepare_dispenser_power", {"elapsed_s": value})
    assert sim.state.virtual_time_s == 0
    clock.now += 3
    sim.read_vacuum_pressure()
    assert sim.timing["wall_elapsed_s"] == 10


def test_current_changes_and_shutdown_evolve_previous_state() -> None:
    clock = Clock()
    sim = make(clock)
    sim.prepare_dispenser_power(elapsed_s=20)
    sim.enable_dispenser_output(sim.confirmation_literal, elapsed_s=30)
    sim.set_dispenser_current(0.2, 0.0, elapsed_s=40)
    assert sim.state.temperature == 0
    reference = make(lambda: 0.0)
    reference.prepare_dispenser_power()
    reference.enable_dispenser_output(reference.confirmation_literal)
    reference.set_dispenser_current(0.2, 0.0)
    reference.advance(120)
    sim.set_dispenser_current(0.4, 0.2, elapsed_s=120)
    assert sim.state.temperature == reference.state.temperature
    reference.set_dispenser_current(0.4, 0.2)
    reference.advance(60)
    clock.now += 60
    sim.shutdown_dispenser_power()
    assert sim.state.temperature == reference.state.temperature
    assert not sim.state.ch1_output_on
    assert sim.timing["requested_elapsed_s"] == 0


def test_failed_action_keeps_accepted_time() -> None:
    sim = make(lambda: 0.0)
    with pytest.raises(SimulationError, match="prepared"):
        sim.enable_dispenser_output(sim.confirmation_literal, elapsed_s=45)
    assert sim.state.virtual_time_s == 45
    assert sim.timing["advanced_s"] == 45


def test_long_intervals_keep_integration_chunks_and_unclamped_wall_floor() -> None:
    clock = Clock()
    sim = make(clock)
    calls: list[float] = []
    integrate = sim.advance

    def advance(seconds: float) -> None:
        calls.append(seconds)
        integrate(seconds)

    sim.advance = advance
    sim.read_vacuum_pressure(elapsed_s=86400)
    assert calls == [3600.0] * 24
    calls.clear()
    clock.now += 90001
    sim.read_vacuum_pressure()
    assert calls == [3600.0] * 25 + [1.0]
    assert sim.timing["advanced_s"] == 90001
    assert sim.state.virtual_time_s == 176401
    assert sim.state.rb_remaining == sim.initial_rb
    assert sim.state.impurity_remaining == sim.initial_impurity
