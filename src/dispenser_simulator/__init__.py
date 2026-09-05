"""Deterministic, hardware-free dispenser-conditioning simulator."""

from .metadata import SIMULATOR_VERSION
from .model import (
    HiddenSimulatorConfig,
    SimulatedDispenser,
    SimulationError,
    ToolRouter,
)

__all__ = [
    "SIMULATOR_VERSION",
    "HiddenSimulatorConfig",
    "SimulatedDispenser",
    "SimulationError",
    "ToolRouter",
]

__version__ = SIMULATOR_VERSION
