"""Typed extension boundary for future explicitly registered instruments."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ScalarChannelDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    unit: str = Field(min_length=1, max_length=64)
    read_only: Literal[True] = True


class TraceChannelDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    x_unit: str = Field(min_length=1, max_length=64)
    y_unit: str = Field(min_length=1, max_length=64)
    raw_on_demand: Literal[True] = True


class NamedControlDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=256)
    input_schema: dict[str, Any]


class TraceArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str = Field(min_length=1, max_length=128)
    channel_id: str = Field(min_length=1, max_length=128)
    point_count: int = Field(ge=0)
    observed_at: str = Field(min_length=1, max_length=64)


class ScalarObservationChannel(Protocol):
    @property
    def descriptor(self) -> ScalarChannelDescriptor: ...

    def read(self) -> Mapping[str, Any]: ...


class TraceCaptureChannel(Protocol):
    @property
    def descriptor(self) -> TraceChannelDescriptor: ...

    def capture(self) -> TraceArtifactReference: ...

    def read_points(
        self, artifact_id: str, *, after: int, limit: int
    ) -> Mapping[str, Any]: ...


class OptionalNamedControl(Protocol):
    @property
    def descriptor(self) -> NamedControlDescriptor: ...

    def invoke(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...


class OptionalCapabilityRegistry:
    """Explicit adapters only; no arbitrary execute or driver discovery."""

    def __init__(
        self,
        *,
        scalar_channels: tuple[ScalarObservationChannel, ...] = (),
        trace_channels: tuple[TraceCaptureChannel, ...] = (),
        named_controls: tuple[OptionalNamedControl, ...] = (),
    ) -> None:
        self.scalar_channels = scalar_channels
        self.trace_channels = trace_channels
        self.named_controls = named_controls
        identifiers = [
            *(item.descriptor.channel_id for item in scalar_channels),
            *(item.descriptor.channel_id for item in trace_channels),
            *(item.descriptor.control_id for item in named_controls),
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Optional capability identifiers must be unique")

    def public_descriptors(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "scalar_channels": [
                item.descriptor.model_dump(mode="json") for item in self.scalar_channels
            ],
            "trace_channels": [
                item.descriptor.model_dump(mode="json") for item in self.trace_channels
            ],
            "named_controls": [
                item.descriptor.model_dump(mode="json") for item in self.named_controls
            ],
        }
