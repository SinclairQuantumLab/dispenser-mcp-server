"""Read-only synchronous client for the Pfeiffer HiCube Neo PVViewer model.

The public client owns one ``asyncua.sync.Client`` session, resolves the vendor
namespace by URI, performs exact batched Value reads, and normalizes them into a
station snapshot. Discovery uses the same protocol implementation and bounded
worker threads; no function in this module writes a Value or calls a Method.
"""

from __future__ import annotations

import ipaddress
import math
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from asyncua import ua
from asyncua.sync import Client

PVVIEWER_NAMESPACE_URI = "urn:pvviewer:NodeOPCUA-Server"
OPC_UA_PORT = 4840


class HiCubeNeoReadError(RuntimeError):
    """Report a snapshot that cannot be read or normalized without data loss.

    Transport exceptions retain their original types. This exception is used
    for relay-owned validation failures such as bad OPC UA quality, malformed
    values, missing required Values, or an invalid result count.
    """


@dataclass(frozen=True)
class HiCubeNeoCandidate:
    """Describe one open OPC UA endpoint checked during network discovery.

    ``verified`` is true only after the PVViewer namespace resolves and the
    P1/TC 80 serial Value passes the same quality and normalization rules used
    by normal acquisition. Unverified candidates retain a concise error.
    """

    ip_address: str
    endpoint: str
    verified: bool
    serial_number: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TelemetryNode:
    """Define one exact read-only PVViewer node and its normalization contract.

    ``attribute`` is the stable source-facing Python name. ``identifier`` is
    the exact vendor String NodeId and must not be inferred by browsing.
    ``required`` controls whether an absent Value rejects the whole snapshot.
    """

    attribute: str
    identifier: str
    kind: Literal["float", "int", "str"]
    required: bool = False
    decimal_places: int | None = None


TELEMETRY_NODES = (
    TelemetryNode("serial_number", "P1_355_Serial No", "str", required=True),
    TelemetryNode("p1_error_code", "P1_303_Error code", "str"),
    TelemetryNode("p1_actual_speed_hz", "P1_309_ActualSpd", "float", required=True),
    TelemetryNode(
        "p1_drive_current_a",
        "P1_310_DrvCurrent",
        "float",
        decimal_places=2,
    ),
    TelemetryNode("p1_pump_operating_hours_h", "P1_311_OpHrsPump", "float"),
    TelemetryNode(
        "p1_drive_voltage_v",
        "P1_313_DrvVoltage",
        "float",
        decimal_places=2,
    ),
    TelemetryNode("p1_drive_power_w", "P1_316_DrvPower", "float"),
    TelemetryNode("p1_pump_cycles", "P1_319_PumpCycles", "int"),
    TelemetryNode(
        "p1_pump_bottom_temperature_deg_c",
        "P1_330_TempPmpBot",
        "float",
    ),
    TelemetryNode("p2_error_code", "P2_303_Error code", "str"),
    TelemetryNode("p2_actual_speed_hz", "P2_309_ActualSpd", "float", required=True),
    TelemetryNode(
        "p2_drive_current_a",
        "P2_310_DrvCurrent",
        "float",
        decimal_places=2,
    ),
    TelemetryNode("p2_pump_operating_hours_h", "P2_311_OpHrsPump", "float"),
    TelemetryNode(
        "p2_drive_voltage_v",
        "P2_313_DrvVoltage",
        "float",
        decimal_places=2,
    ),
    TelemetryNode("p2_drive_power_w", "P2_316_DrvPower", "float"),
    TelemetryNode("p2_pump_temperature_deg_c", "P2_330_TempPump", "float"),
    TelemetryNode("g1_pressure_mbar", "G1_pressure", "float", required=True),
)


@dataclass(frozen=True)
class HiCubeNeoSample:
    """Hold one normalized station snapshot and its collector UTC timestamp.

    Source-facing names remain independent of the Grafana-visible schema.
    Optional attributes are ``None`` only for an absent optional vendor node or
    a Good DataValue that contains no value.
    """

    observed_at: datetime
    serial_number: str
    p1_error_code: str | None = None
    p1_actual_speed_hz: float | None = None
    p1_drive_current_a: float | None = None
    p1_pump_operating_hours_h: float | None = None
    p1_drive_voltage_v: float | None = None
    p1_drive_power_w: float | None = None
    p1_pump_cycles: int | None = None
    p1_pump_bottom_temperature_deg_c: float | None = None
    p2_error_code: str | None = None
    p2_actual_speed_hz: float | None = None
    p2_drive_current_a: float | None = None
    p2_pump_operating_hours_h: float | None = None
    p2_drive_voltage_v: float | None = None
    p2_drive_power_w: float | None = None
    p2_pump_temperature_deg_c: float | None = None
    g1_pressure_mbar: float | None = None


def _normalize_number(value: Any, *, attribute: str) -> float:
    """Return a finite float while rejecting booleans and nonnumeric values."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HiCubeNeoReadError(f"{attribute} is not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise HiCubeNeoReadError(f"{attribute} is not finite")
    return number


def _normalize_integer(value: Any, *, attribute: str) -> int:
    """Return an integer from the device's integral numeric representation."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HiCubeNeoReadError(f"{attribute} is not numeric")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise HiCubeNeoReadError(f"{attribute} is not an integral value")
    return int(number)


def _normalize_string(value: Any, *, attribute: str) -> str:
    """Return a device string without coercing another OPC UA datatype."""

    if not isinstance(value, str):
        raise HiCubeNeoReadError(f"{attribute} is not a string")
    return value


def _clean_serial_number(value: str) -> str:
    """Strip the P1/TC 80 serial and reject an empty device identity."""

    serial_number = value.strip()
    if not serial_number:
        raise HiCubeNeoReadError("serial_number is empty")
    return serial_number


def _normalize_value(node: TelemetryNode, data_value: ua.DataValue) -> Any:
    """Validate one DataValue's quality and apply its declared type contract."""

    status = data_value.StatusCode
    if not status.is_good():
        if not node.required and status.value == ua.StatusCodes.BadNodeIdUnknown:
            return None
        raise HiCubeNeoReadError(f"{node.attribute} status is {status.name}")

    variant = data_value.Value
    value = None if variant is None else variant.Value
    if value is None:
        if not node.required:
            return None
        raise HiCubeNeoReadError(f"{node.attribute} has no value")

    if node.kind == "float":
        number = _normalize_number(value, attribute=node.attribute)
        if node.decimal_places is not None:
            return round(number, node.decimal_places)
        return number
    if node.kind == "int":
        return _normalize_integer(value, attribute=node.attribute)
    return _normalize_string(value, attribute=node.attribute)


def _tcp_port_is_open(ip_address: str, port: int, timeout_s: float) -> bool:
    """Return whether a bounded TCP connection to one candidate succeeds."""

    try:
        with socket.create_connection((ip_address, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _validate_port(port: int) -> int:
    """Validate a TCP port without accepting booleans as integers."""

    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise ValueError("port must be an integer between 1 and 65535")
    return port


class HiCubeNeoClient:
    """Own one synchronous read-only HiCube Neo OPC UA session.

    The class wraps ``asyncua.sync.Client`` rather than exposing asyncua's
    asynchronous API to the relay. ``connect`` resolves the current namespace
    index and exact String NodeIds. ``read_sample`` issues one Read service for
    every mapped Value, validates quality before normalization, and timestamps
    the completed snapshot with the collector clock. ``close`` is idempotent
    and also stops the synchronous wrapper's private event-loop thread.

    Construction accepts one bare hostname or IP literal, a separately
    validated TCP port, and an operation timeout without opening the network.
    IPv6 hosts are bracketed only while building the OPC UA endpoint.

    The class does not browse for approximate matches, enable asyncua automatic
    reconnection, write Values, or call Methods. Callers own recovery and may
    replace a failed instance connection once before counting a failure.
    """

    def __init__(
        self,
        *,
        host: str,
        timeout_s: float,
        port: int = OPC_UA_PORT,
        client_factory: Callable[..., Any] = Client,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Configure an endpoint without opening a network connection."""

        endpoint_host = f"[{host}]" if ":" in host else host
        self.port = _validate_port(port)
        self.endpoint = f"opc.tcp://{endpoint_host}:{self.port}"
        self.timeout_s = timeout_s
        self._client_factory = client_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client: Any | None = None
        self._nodes: list[Any] = []

    @property
    def is_connected(self) -> bool:
        """Return whether this adapter currently owns a connected client."""

        return self._client is not None

    @classmethod
    def discover_devices(
        cls,
        network: str,
        *,
        port: int = OPC_UA_PORT,
        timeout_s: float = 2.0,
        concurrency: int = 32,
        max_hosts: int = 4096,
        tcp_probe: Callable[[str, int, float], bool] = _tcp_port_is_open,
        client_factory: Callable[..., Any] = Client,
    ) -> list[HiCubeNeoCandidate]:
        """Scan one explicit network with bounded workers and verify candidates.

        Closed ports are omitted. Every open port is checked with the same
        read-only namespace and P1/TC 80 serial path as normal acquisition.
        Results retain ascending network-address order even though probes run
        concurrently.
        """

        port = _validate_port(port)
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        parsed_network = ipaddress.ip_network(network, strict=False)
        if parsed_network.num_addresses > max_hosts + 2:
            raise ValueError(f"network contains more than {max_hosts} host addresses")
        addresses = [str(address) for address in parsed_network.hosts()]
        if len(addresses) > max_hosts:
            raise ValueError(f"network contains more than {max_hosts} host addresses")
        if not addresses:
            return []

        def probe(ip_address: str) -> HiCubeNeoCandidate | None:
            """Probe and verify one address without allowing an exception to escape."""

            if not tcp_probe(ip_address, port, timeout_s):
                return None
            client = cls(
                host=ip_address,
                port=port,
                timeout_s=timeout_s,
                client_factory=client_factory,
            )
            try:
                client.connect()
                serial_number = client.read_serial_number()
                return HiCubeNeoCandidate(
                    ip_address=ip_address,
                    endpoint=client.endpoint,
                    verified=True,
                    serial_number=serial_number,
                )
            except Exception as error:
                return HiCubeNeoCandidate(
                    ip_address=ip_address,
                    endpoint=client.endpoint,
                    verified=False,
                    error=f"{type(error).__name__}: {error}",
                )
            finally:
                with suppress(Exception):
                    client.close()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(probe, addresses))
        return [candidate for candidate in results if candidate is not None]

    def connect(self) -> None:
        """Open one session and resolve exact nodes through the namespace URI."""

        if self._client is not None:
            return
        client = self._client_factory(url=self.endpoint, timeout=self.timeout_s)
        try:
            client.connect()
            namespace_index = client.get_namespace_index(PVVIEWER_NAMESPACE_URI)
            nodes = [
                client.get_node(ua.StringNodeId(node.identifier, namespace_index))
                for node in TELEMETRY_NODES
            ]
        except BaseException:
            with suppress(Exception):
                client.disconnect()
            raise
        self._client = client
        self._nodes = nodes

    def _read_values(
        self,
        nodes: list[Any],
        *,
        timestamps: ua.TimestampsToReturn,
    ) -> list[ua.DataValue]:
        """Issue one explicit synchronous Read service for the supplied nodes."""

        if self._client is None or not nodes:
            raise HiCubeNeoReadError("HiCube Neo client is not connected")
        parameters = ua.ReadParameters(
            MaxAge=0,
            TimestampsToReturn=timestamps,
            NodesToRead=[
                ua.ReadValueId(
                    NodeId=node.nodeid,
                    AttributeId=ua.AttributeIds.Value,
                )
                for node in nodes
            ],
        )
        return nodes[0].read_params(parameters)

    def read_sample(self) -> HiCubeNeoSample:
        """Read all configured Values in one call and normalize one snapshot."""

        data_values = self._read_values(
            self._nodes,
            timestamps=ua.TimestampsToReturn.Both,
        )
        if len(data_values) != len(TELEMETRY_NODES):
            raise HiCubeNeoReadError(
                "snapshot returned an unexpected number of DataValues"
            )

        values = {
            node.attribute: _normalize_value(node, data_value)
            for node, data_value in zip(TELEMETRY_NODES, data_values, strict=True)
        }
        values["serial_number"] = _clean_serial_number(values["serial_number"])
        observed_at = self._clock()
        if observed_at.utcoffset() is None:
            raise HiCubeNeoReadError("collector clock returned a naive timestamp")
        return HiCubeNeoSample(
            observed_at=observed_at.astimezone(UTC),
            **values,
        )

    def read_serial_number(self) -> str:
        """Read the P1/TC 80 drive serial used to verify one candidate."""

        data_values = self._read_values(
            self._nodes[:1],
            timestamps=ua.TimestampsToReturn.Neither,
        )
        if len(data_values) != 1:
            raise HiCubeNeoReadError("serial read returned an unexpected result count")
        value = _normalize_value(TELEMETRY_NODES[0], data_values[0])
        return _clean_serial_number(value)

    def close(self) -> None:
        """Disconnect idempotently and release the wrapper event-loop thread."""

        client = self._client
        self._client = None
        self._nodes = []
        if client is not None:
            client.disconnect()
