# Pressure Observation Contract

## Scope

The pressure slice exposes one bounded read-only tool within the combined
server. It does not scan a network, browse OPC UA nodes, access InfluxDB, read
credentials, or control any HiCube Neo component. The separate power tools are
specified in [power-control-contract.md](power-control-contract.md).

The operator-selected commissioned `hicube_neo_client.py` remains the source of
truth for the exact PVViewer namespace, NodeIds, batched Value read, OPC UA
status validation, and source normalization. This component loads that file
from operator-only startup configuration; it does not copy or modify it.

## Tool

### `read_vacuum_pressure`

Description: Read one G1 total-pressure snapshot; never infer dispenser
activation.

Input schema:

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false,
  "title": "read_vacuum_pressureArguments"
}
```

There are no model-facing device, host, port, timeout, file-path, or discovery
arguments. Any non-empty argument object is rejected as a sanitized MCP tool
error before the observation source is called.

Successful structured output:

| Field | Type | Contract |
| --- | --- | --- |
| `observed_at` | RFC 3339 date-time | Collector timestamp normalized to UTC |
| `pressure_mbar` | positive number | Native PVViewer `G1_pressure` total pressure |
| `pressure_torr` | positive number | `pressure_mbar * 760 / 1013.25` |
| `source` | string | `pfeiffer_hicube_neo.pvviewer.g1_pressure` |
| `p1_drive_serial_number` | non-empty string | P1/TC 80 drive identity, not a gauge or station serial |
| `is_total_gauge_pressure` | boolean | Always `true` |
| `is_rubidium_partial_pressure` | boolean | Always `false` |
| `verifies_dispenser_activation` | boolean | Always `false` |

The server publishes a closed output schema. `source` and the three
interpretation fields are JSON Schema constants, both pressure values have an
exclusive minimum of zero, `p1_drive_serial_number` has a minimum length of
one, and every listed field is required. Results are one fixed-size snapshot;
there is no pagination or continuation state.

Tool annotations:

```json
{
  "readOnlyHint": true,
  "destructiveHint": false,
  "idempotentHint": true,
  "openWorldHint": true
}
```

`idempotentHint` describes the absence of side effects; repeated calls may
return different measurements because pressure and observation time change.

## Execution and error behavior

One synchronous handler call performs this exact sequence:

1. Load the configured commissioned client class.
2. Create one client for the configured endpoint.
3. Connect without discovery.
4. Call `read_sample()` once.
5. Extract `observed_at`, `g1_pressure_mbar`, and the P1 drive serial.
6. Close the client in `finally`, including after a failed connect or read.
7. Validate a timezone-aware timestamp, finite positive pressure, and non-empty
   drive serial; normalize the timestamp to UTC and derive Torr.

Expected source, connection, quality, normalization, and close failures produce
an MCP tool error. The model-visible message contains no device address, local
path, or raw driver exception. Operator diagnostics are written to stderr;
stdout remains reserved for MCP stdio frames.

The configured operation timeout is bounded to 0.1 through 60 seconds. A failed
call has `isError: true` and no structured output. Retrying does not create an
external side effect, but callers should not loop automatically: a retry is
appropriate only when an operator judges the failure transient or corrects the
startup configuration or device state.

## Interpretation boundary

G1 reports total gauge pressure. It does not isolate rubidium partial pressure.
This tool performs no pressure-trace classification, activation assessment, or
independent verification of dispenser activation or function. Those later
capabilities must preserve this distinction in their own versioned contracts.
