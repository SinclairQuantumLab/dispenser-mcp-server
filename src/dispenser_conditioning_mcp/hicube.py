"""Adapter around the commissioned read-only HiCube Neo client."""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any, Protocol, cast

from dispenser_conditioning_mcp.domain import (
    PressureObservationError,
    RawPressureObservation,
)

logger = logging.getLogger(__name__)


class HiCubeSample(Protocol):
    """Fields consumed from the commissioned client's normalized sample."""

    observed_at: datetime
    g1_pressure_mbar: float | None
    serial_number: str


class HiCubeClient(Protocol):
    """Read-only portion of the commissioned client used by this adapter."""

    def connect(self) -> None: ...

    def read_sample(self) -> HiCubeSample: ...

    def close(self) -> None: ...


type HiCubeClientFactory = Callable[..., HiCubeClient]


def validate_hicube_client_installation(client_file: Path) -> None:
    """Import the commissioned client without constructing or connecting it."""

    _load_client_factory(client_file)


class HiCubeNeoPressureSource:
    """Create, use, and close one commissioned client for each observation."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        timeout_s: float,
        client_file: Path | None = None,
        client_factory: HiCubeClientFactory | None = None,
    ) -> None:
        if (client_file is None) == (client_factory is None):
            raise ValueError("Provide exactly one client_file or client_factory.")
        self._host = host
        self._port = port
        self._timeout_s = timeout_s
        self._client_file = client_file
        self._client_factory = client_factory
        self._read_lock = Lock()

    def read(self) -> RawPressureObservation:
        """Read one batch snapshot without discovery, writes, or method calls."""

        with self._read_lock:
            client: HiCubeClient | None = None
            observation: RawPressureObservation | None = None
            failure: Exception | None = None
            try:
                factory = self._client_factory or _load_client_factory(
                    cast(Path, self._client_file)
                )
                client = factory(
                    host=self._host,
                    port=self._port,
                    timeout_s=self._timeout_s,
                )
                client.connect()
                sample = client.read_sample()
                if sample.g1_pressure_mbar is None:
                    raise ValueError("Required G1 pressure is absent.")
                observation = RawPressureObservation(
                    observed_at=sample.observed_at,
                    pressure_mbar=sample.g1_pressure_mbar,
                    p1_drive_serial_number=sample.serial_number,
                )
            except Exception as error:
                failure = error
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as error:
                        logger.error(
                            "Closing the HiCube Neo client failed.",
                            exc_info=(type(error), error, error.__traceback__),
                        )
                        if failure is None:
                            failure = error

            if failure is not None:
                logger.error(
                    "The configured HiCube Neo pressure read failed.",
                    exc_info=(type(failure), failure, failure.__traceback__),
                )
                raise PressureObservationError(
                    "Vacuum pressure is unavailable from the configured read-only "
                    "HiCube Neo source."
                ) from failure
            if observation is None:  # pragma: no cover - defensive invariant
                raise PressureObservationError(
                    "Vacuum pressure is unavailable from the configured read-only "
                    "HiCube Neo source."
                )
            return observation


def _load_client_factory(client_file: Path) -> HiCubeClientFactory:
    """Load the commissioned class from one operator-configured local file."""

    module_name = "_dispenser_conditioning_commissioned_hicube_client"
    spec = importlib.util.spec_from_file_location(module_name, client_file)
    if spec is None or spec.loader is None:
        raise ImportError("The configured client module cannot be loaded.")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        factory = _client_factory_from_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return factory


def _client_factory_from_module(module: ModuleType) -> HiCubeClientFactory:
    value: Any = getattr(module, "HiCubeNeoClient", None)
    if not callable(value):
        raise ImportError("The configured module has no HiCubeNeoClient class.")
    return cast(HiCubeClientFactory, value)
