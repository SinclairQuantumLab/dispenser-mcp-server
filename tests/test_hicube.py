import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dispenser_conditioning_mcp.domain import PressureObservationError
from dispenser_conditioning_mcp.hicube import HiCubeNeoPressureSource

EXPECTED_VENDORED_HICUBE_SHA256 = (
    "a7bdbf45836f6c92d149f0cdb2dee439d17fcd6b1ce3836404df23fa1c0a4325"
)


def test_vendored_hicube_client_matches_recorded_canonical_source() -> None:
    root = Path(__file__).parents[1]
    client = root / "dependencies/hicube/hicube_neo_client.py"
    provenance = (root / "dependencies/hicube/PROVENANCE.md").read_text(
        encoding="utf-8"
    )

    assert hashlib.sha256(client.read_bytes()).hexdigest() == (
        EXPECTED_VENDORED_HICUBE_SHA256
    )
    assert EXPECTED_VENDORED_HICUBE_SHA256 in provenance
    assert "25741296ea2bc536e6ca6b51645ce92ef953c0ca" in provenance


class FakeClient:
    def __init__(
        self,
        events: list[str],
        *,
        sample: SimpleNamespace | None = None,
        read_error: Exception | None = None,
        close_error: Exception | None = None,
        **_: Any,
    ) -> None:
        self._events = events
        self._sample = sample
        self._read_error = read_error
        self._close_error = close_error
        events.append("create")

    def connect(self) -> None:
        self._events.append("connect")

    def read_sample(self) -> SimpleNamespace:
        self._events.append("read_sample")
        if self._read_error is not None:
            raise self._read_error
        assert self._sample is not None
        return self._sample

    def close(self) -> None:
        self._events.append("close")
        if self._close_error is not None:
            raise self._close_error

    @classmethod
    def discover_devices(cls, *_: object, **__: object) -> None:
        raise AssertionError("Discovery must never be called.")


def test_source_creates_reads_and_closes_one_client_without_discovery() -> None:
    events: list[str] = []
    sample = SimpleNamespace(
        observed_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        g1_pressure_mbar=2.5e-7,
        serial_number="TC80-123",
    )

    def factory(**kwargs: Any) -> FakeClient:
        assert kwargs == {"host": "example.test", "port": 4840, "timeout_s": 3.0}
        return FakeClient(events, sample=sample, **kwargs)

    source = HiCubeNeoPressureSource(
        host="example.test",
        port=4840,
        timeout_s=3.0,
        client_factory=cast(Any, factory),
    )

    result = source.read()

    assert events == ["create", "connect", "read_sample", "close"]
    assert result.pressure_mbar == 2.5e-7
    assert result.p1_drive_serial_number == "TC80-123"


def test_source_closes_client_and_sanitizes_driver_failure() -> None:
    events: list[str] = []
    secret_marker = "opc.tcp://private-host:4840/internal/path"

    def factory(**kwargs: Any) -> FakeClient:
        return FakeClient(events, read_error=RuntimeError(secret_marker), **kwargs)

    source = HiCubeNeoPressureSource(
        host="example.test",
        port=4840,
        timeout_s=3.0,
        client_factory=cast(Any, factory),
    )

    with pytest.raises(PressureObservationError) as captured:
        source.read()

    assert events == ["create", "connect", "read_sample", "close"]
    assert secret_marker not in str(captured.value)


def test_source_treats_close_failure_as_sanitized_read_failure() -> None:
    events: list[str] = []
    sample = SimpleNamespace(
        observed_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        g1_pressure_mbar=2.5e-7,
        serial_number="TC80-123",
    )
    secret_marker = "private-close-detail"

    def factory(**kwargs: Any) -> FakeClient:
        return FakeClient(
            events,
            sample=sample,
            close_error=RuntimeError(secret_marker),
            **kwargs,
        )

    source = HiCubeNeoPressureSource(
        host="example.test",
        port=4840,
        timeout_s=3.0,
        client_factory=cast(Any, factory),
    )

    with pytest.raises(PressureObservationError) as captured:
        source.read()

    assert events == ["create", "connect", "read_sample", "close"]
    assert secret_marker not in str(captured.value)


def test_source_loads_operator_configured_client_file_offline(tmp_path: Path) -> None:
    client_file = tmp_path / "hicube_neo_client.py"
    client_file.write_text(
        """
from datetime import UTC, datetime
from types import SimpleNamespace

class HiCubeNeoClient:
    def __init__(self, *, host, port, timeout_s):
        assert (host, port, timeout_s) == ("example.test", 4840, 3.0)

    def connect(self):
        return None

    def read_sample(self):
        return SimpleNamespace(
            observed_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
            g1_pressure_mbar=3.0e-7,
            serial_number="TC80-FILE",
        )

    def close(self):
        return None
""".lstrip(),
        encoding="utf-8",
    )
    source = HiCubeNeoPressureSource(
        host="example.test",
        port=4840,
        timeout_s=3.0,
        client_file=client_file,
    )

    result = source.read()

    assert result.pressure_mbar == 3.0e-7
    assert result.p1_drive_serial_number == "TC80-FILE"
