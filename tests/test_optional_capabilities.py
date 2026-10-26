from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dispenser_conditioning_mcp.optional_capabilities import (
    OptionalCapabilityRegistry,
    ScalarChannelDescriptor,
    TraceArtifactReference,
    TraceChannelDescriptor,
)


class FakeScalar:
    descriptor = ScalarChannelDescriptor(
        channel_id="ion-gauge-1",
        label="Ion gauge scalar",
        unit="mbar",
        read_only=True,
    )

    def read(self) -> Mapping[str, Any]:
        return {"value": 1e-9, "observed_at": "2026-10-26T12:00:00Z"}


class FakeTrace:
    descriptor = TraceChannelDescriptor(
        channel_id="scope-ch1",
        label="Scope channel 1",
        x_unit="s",
        y_unit="V",
        raw_on_demand=True,
    )

    def capture(self) -> TraceArtifactReference:
        return TraceArtifactReference(
            artifact_id="trace-1",
            channel_id="scope-ch1",
            point_count=2,
            observed_at="2026-10-26T12:00:00Z",
        )

    def read_points(
        self, artifact_id: str, *, after: int, limit: int
    ) -> Mapping[str, Any]:
        assert (artifact_id, after, limit) == ("trace-1", 0, 2)
        return {"artifact_id": artifact_id, "points": [[0.0, 0.0], [1.0, 1.0]]}


def test_fake_scalar_and_trace_adapters_satisfy_typed_descriptor_contract() -> None:
    scalar = FakeScalar()
    trace = FakeTrace()
    registry = OptionalCapabilityRegistry(
        scalar_channels=(scalar,), trace_channels=(trace,)
    )

    assert scalar.read()["value"] == 1e-9
    artifact = trace.capture()
    assert artifact.point_count == 2
    assert trace.read_points(artifact.artifact_id, after=0, limit=2)["points"]
    assert registry.public_descriptors() == {
        "scalar_channels": [scalar.descriptor.model_dump(mode="json")],
        "trace_channels": [trace.descriptor.model_dump(mode="json")],
        "named_controls": [],
    }
