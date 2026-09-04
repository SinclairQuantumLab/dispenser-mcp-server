# SPD3000 Power-Control MCP Contract

## Scope and status

Version 0.4.3 defines the smallest conditioning-oriented control surface around the
hardware-validated public semantic API in the pinned
`dependencies/py-siglent-spd3000` Git submodule. The MCP loads the submodule's
`src` directory directly; no built driver wheel is required for development.
Driver-level physical validation and MCP-level integration validation are
reported separately:

```json
{
  "driver_hardware_validation_status":"validated_on_physical_instrument_via_gateway",
  "mcp_read_path_validation_status":"validated_on_physical_instrument_via_authenticated_gateway",
  "mcp_actuation_validation_status":"not_yet_validated_with_connected_dispenser | validated_on_unloaded_physical_instrument_via_authenticated_gateway"
}
```

The actuation status is selected by the startup-bound acceptance context. The
unloaded-HIL path was physically exercised before the durable interlock was
introduced; the v0.4.3 interlock is separately labeled offline-only in its
structured diagnostics. This interface does not implement a conditioning state
machine, pressure trip, activation inference, persistent output lease, watchdog,
audit log, or physical interlock.

## Hidden immutable policy

Startup TOML must bind the exact expected serial, one acceptance context, one
fixed compliance voltage, and an explicit control-enable flag. The expected
model, topology, channel, current ceilings, and upward step are contract
constants. `unloaded_hil` additionally requires one absolute protected
`unloaded_hil_state_file` in `settings/mcp-settings.toml`.
The MCP client cannot read or change connection details and cannot change any
policy value.

| Profile | Native channel | Required live mode | Factor | Instrument capability | Deployment ceiling |
| --- | --- | --- | --- | --- | --- |
| `parallel_ch1` | fixed CH1 | `parallel` | 2 | 6.4 A load | 2.4 A native / 4.8 A load |

No other topology or native channel is accepted at startup. The server never
writes a tracking-mode command. Native CH1 current setpoint = requested
load-current limit / 2. This deployment adds a
2.4 A native/4.8 A load-current ceiling, so accepted targets are bounded by:

```text
min(topology hardware ceiling, fixed deployment/workflow ceiling of 4.8 A)
```

Only commanded current is topology-scaled. Native CH1 current, voltage, and
power measurements are returned without manufacturing a load-level parallel
measurement.

The accepted startup contexts are deliberately distinct:

| Acceptance context | Intended physical state | Advertised confirmation input | Required literal |
| --- | --- | --- | --- |
| `production_dispenser` | Approved dispenser wired to parallel CH1 output | `parallel_connection_confirmation` | `confirmed_parallel_ch1` |
| `unloaded_hil` | No dispenser or unapproved load connected; operator-approved metrology wiring may be present | `unloaded_hil_connection_confirmation` | `confirmed_no_dispenser_or_unapproved_load_connected` |

Each server instance advertises only the input selected at startup. The other
field is rejected as an unknown property, and the controller independently
rejects a mismatched literal before opening a driver session. The unloaded HIL
context must never be used for a connected dispenser.

## Tools

Every input is a closed JSON object with `additionalProperties: false`.
Unknown properties are rejected before a controller or driver call.
Model-facing numeric mutation fields accept JSON integer or floating-point
numbers only. Numeric strings and booleans are rejected before controller,
session, or device access.

### `read_dispenser_power_state`

Input: `{}`.

Reads exact identity and one configured-channel snapshot. Identity mismatch is
a sanitized error but never a write. The result includes UTC `observed_at`,
source, configured topology/factor/channel, expected and live operating mode,
`topology_matches`, manufacturer/model/serial/firmware, native voltage and
current setpoints, `commanded_load_current_limit_a`, native-channel V/I/power
measurements, output/regulation, `compliance_voltage_matches`, prepared state,
fixed safety limits, driver hardware-validation status, MCP read-path validation
status, and MCP actuation-validation status. Fixed safety limits include the
startup acceptance context, its required enable-confirmation literal, and the
fixed `unloaded_hil_safe_measured_current_abs_a=0.001` threshold.
The state also includes a read-only `unloaded_hil_interlock` object. Production
reports `not_applicable`. Unloaded HIL reports `unlatched`, `latched`, or
`unavailable_fail_closed`, the immutable first trip when present, reset authority
`out_of_band_human_only`, and an explicit offline-only interlock validation
status. Reading diagnostics never creates, updates, or clears latch state.

Annotations: read-only true, destructive false, idempotent true, open-world
true.

### `prepare_dispenser_power`

Input: `{}`.

After control-enable, identity, and topology verification:

1. command bound output off and verify it off;
2. set native current to zero and verify it;
3. set the fixed compliance voltage;
4. verify output off, zero current, compliance voltage, and topology in a fresh
   state snapshot.

Any post-write failure invokes one best-effort output-off then zero-current
sequence. No command is retried automatically.

Annotations: read-only false, destructive true, idempotent true, open-world
true. The destructive hint reflects deliberate overwriting of prior setpoints
and output state even though the sequence is safety-oriented.

### `enable_dispenser_output`

Production-dispenser input:

```json
{"parallel_connection_confirmation":"confirmed_parallel_ch1"}
```

Unloaded-HIL input:

```json
{"unloaded_hil_connection_confirmation":"confirmed_no_dispenser_or_unapproved_load_connected"}
```

The production literal may be supplied only after asking the human operator
immediately before the call to verify the approved physical parallel CH1
dispenser wiring. The unloaded-HIL literal may be supplied only after asking the
human immediately before the call to verify that no dispenser or unapproved load
is connected. Operator-approved metrology wiring, including a voltmeter or no
wiring, may be present. Instrument tracking mode cannot verify either physical
state. Each literal is a caller attestation and cannot independently authenticate
human provenance; the MCP host must enforce the human-in-the-loop approval before
every enable action.

Requires a fresh exact identity, topology match, output off, native current
zero, and fixed compliance-voltage match. It then commands output on and
verifies live mode, output state, native current still zero, and compliance
voltage still matched. A repeated call while already on is rejected without a
write. If enabling changes current or voltage unexpectedly, the post-write
recovery path runs.

Annotations: read-only false, destructive true, idempotent false, open-world
true. These conservative hints reflect cumulative physical heating.

### `set_dispenser_current`

Input:

```json
{
  "type": "object",
  "properties": {
    "target_current_a": {"type": "number", "minimum": 0, "maximum": 4.8},
    "expected_current_a": {"type": "number", "minimum": 0, "maximum": 6.4}
  },
  "required": ["target_current_a", "expected_current_a"],
  "additionalProperties": false
}
```

Both values are absolute commanded **load-current limits**, not measured load
current. The tool:

1. validates both values and native resolution after topology translation;
2. verifies identity, topology, output on, and fixed compliance voltage;
3. compares the live commanded current with `expected_current_a`;
4. permits an increase only when target minus expected equals the fixed 0.2 A
   load-current step for `parallel_ch1`; permits a decrease;
5. writes the topology-translated native current once;
6. verifies topology, output, compliance voltage, and native readback.

If live current equals target, the tool returns with `wrote_hardware=false`
only when the expected-to-target relationship is itself a permitted transition.
This makes a retry after an uncertain response write-free and cannot perform a
second blind step. Any other live/expected mismatch is rejected before write.

Annotations: read-only false, destructive true, idempotent false, open-world
true. No automatic retry occurs.

### `shutdown_dispenser_power`

Input: `{}`.

After control-enable and exact identity verification, it intentionally does
not require topology match. It commands both CH1 and CH2 outputs off, verifies
both, then zeros both native current setpoints and verifies both. This remains
safe when live mode is parallel and prevents an independently energized CH2
from being ignored after topology drift. Identity mismatch still permits no
write.

Annotations: read-only false, destructive true, idempotent true, open-world
true. It destructively overwrites output/current state but is explicitly
energy-reducing.

This is software shutdown—not an emergency-stop circuit, watchdog, or guarantee
of power removal. If communication or verification fails, an operator must use
physical verification or hardware shutdown.

## Unloaded-HIL post-operation interlock

Before any unloaded-HIL mutating request can open a device session, the protected
provider atomically commits a pending-operation record. If that write or readback
cannot be established, the request is rejected before device access. For this
contract, a mutating power operation is prepare, enable, compare-and-set current
(including a valid write-free replay), or shutdown. After a successful operation,
the controller makes a distinct fresh native-CH1 measured-current query.
Production context does not apply this no-load invariant.

A finite native CH1 measurement in the inclusive fixed band
`[-0.001 A, +0.001 A]` passes. One finite sample with `abs(I) > 0.001 A`, positive
or negative, trips immediately; no averaging or energized debounce is applied.
A query error, unavailable result, or non-finite value also trips fail-closed,
including after a valid write-free compare-and-set replay. The controller uses
the already-durable pending record and performs recovery in this order:

1. command CH1 and CH2 outputs off in one verified semantic batch;
2. query and verify both outputs off;
3. command both native current setpoints to zero in one verified semantic batch;
4. query and verify both current setpoints zero; and
5. query measured native current again and require a finite value inside the same
   inclusive fixed band before reporting verified recovery; and
6. replace the pending record with the structurally valid trip record.

Hardware shutdown therefore does not wait for trip-record persistence. Before
device access, the provider durably publishes both the primary pending record
and a separate pending guard in the same protected directory. If trip
replacement fails, either pending representation remains fail-closed. Only when
the original operation succeeds and its fresh measured-current query is finite
and in-band does the provider publish, directory-sync, and verify a
completed-operation record before retiring the guard. The primary state file is
never deleted during normal MCP operation.

The `0.001 A` absolute threshold is a hard-coded MCP safety policy and appears in
structured safety limits; it is not model- or startup-configurable. It matches one
SPD3303X current resolution increment, but does not establish the absence of a
physical load and is not a representation of the instrument's wider published
readback-current accuracy. Fresh-sample physical characterization is a future
supervised acceptance item, not a reason to weaken the immediate single-sample
trip outside this band.

The tool returns a sanitized explicit interlock error rather than a normal
result. If any hardware check or durable-state commit is uncertain,
it does not report a safe result and requires physical verification or hardware
shutdown. After a durable trip, every later mutating request—including software
shutdown—is rejected before a device session opens; the trip call has already
attempted two-channel shutdown in its original session. Read-only power
diagnostics remain available. If latch state cannot be read or the current
process could not persist a trip, diagnostics report
`unavailable_fail_closed` and mutation remains denied. An unfinished pending
record after restart also reports `unavailable_fail_closed` with
`failure_reason="unfinished_pending_operation"` and rejects mutation before a
session opens.

Trip records use schema version 2 for new v0.4.3 events. Numeric outside-band
events use reason `post_operation_measured_native_current_outside_safe_band` and
retain the signed native-channel observation. Read, unavailable, and non-finite
events use reason `post_operation_measured_native_current_unavailable` with a
null observation. Existing v0.4.1 schema-version-1 records retain reason
`post_operation_nonzero_measured_native_current`; they are accepted only for
compatibility and remain latched without reinterpretation or clearing.

The public output schema is structurally strict rather than relying only on a
runtime check: the legacy v1 variant requires schema 1, its legacy reason, and a
finite signed nonzero number; the v2 outside-band variant requires schema 2, its
outside-band reason, and a finite signed value below `-0.001 A` or above
`+0.001 A`; the v2 unavailable variant requires schema 2, its unavailable reason,
and JSON null. Invalid persisted combinations fail closed without normalization.

The protected provider exposes read-state, begin-operation, complete-operation,
and first-trip recording responsibilities; it has no reset, clear, delete, or
bypass method. No tool schema includes context, state path, or reset control.
Reset belongs to an out-of-band human emergency procedure and a future physical
button/reset service. With the current protected local-file backend, production
ACLs/process isolation must allow the MCP process to manage the state record while
denying the execution agent direct filesystem access. Removing or replacing a
trip or unfinished pending record is an out-of-band privileged reset, never an
MCP action. Physical-button integration remains pending.

The local-file backend stages and `fsync`s a new primary record before atomic
replacement. A separately `fsync`ed pending guard remains authoritative while a
safe completed/trip replacement is published and verified. If completion
publication reports any error—including failure of the POSIX parent-directory
`fsync` after `os.replace`—a fresh provider observes the surviving guard as an
unfinished operation and denies mutation before device access. After a safe
replacement is durable, a lost guard-deletion update can only resurrect the
guard after a crash and cause a conservative fail-closed restart; it cannot
create a false unlatched state.

Windows can transiently deny replacement while a scanner holds a
non-delete-sharing handle. The backend retries only `WinError 5`, `32`, and `33`
with fixed delays of 5, 10, 20, and 40 ms. General permission, path, validation,
and persistence errors are not retried. Pending-marker retries happen before a
device session can open. Trip-record retries happen only after both outputs are
verified off, both native current setpoints are verified zero, and recovery
current is in-band, so they cannot delay trip shutdown. Exhaustion preserves the
previous pending state and the controller remains fail-closed. The guard filename
is derived internally from the operator-bound state path and is not configurable
through MCP. A privileged out-of-band reset must treat the primary state and any
surviving guard as one safety-state boundary after physical verification.

## Error and lifecycle behavior

Each tool call creates, uses, and closes one semantic-driver session. This
deployment accepts only the authenticated gateway connection. Gateway
authentication is loaded from the fixed untracked local file
`settings/py-siglent-spd3000/gateway-auth.toml`. The token never enters tool
arguments or results. A process
lock prevents overlapping control sequences inside this MCP process. The file
contains one root `token` string and is parsed by the driver's strict
`load_gateway_auth(..., required=True)` implementation rather than by duplicate
MCP parsing logic. Device
address, resource, source path, and raw exception text are never included in a
tool error. Identity/policy/state rejections state the safe reason. An uncertain
post-write error explicitly states that output may be unknown and physical
verification or hardware shutdown is required. For `parallel_ch1`, recovery is
considered verified only if both outputs are confirmed off and both native
current setpoints are confirmed zero; a CH2 failure cannot be reported safe.

The adapter enables driver write verification. Each related write group and
each state snapshot is a non-interleaved gateway batch. The in-process lock
cannot arbitrate another program controlling the same supply, and a separate
authorized writer can still act between the MCP's precondition snapshot and
later write batch. Other clients must remain read-only during conditioning
until a workflow-duration output lease exists.

## Excluded surface

There is no model-facing arbitrary SCPI, channel selection, transport selection,
tracking-mode change, network discovery/configuration, save/recall, waveform,
timer, gateway administration, device lock, policy mutation, latch-path
selection, interlock reset, or bypass.

## Minimal unloaded-HIL acceptance sequence

The recommended first-run procedure uses an operator-approved 1.0 V fixed
compliance voltage and only one 0.2 A commanded-load step. Parallel tracking mode must be set
out-of-band with outputs off because MCP intentionally exposes no tracking
control.

After a control-disabled identity/topology read, restart with the same
`unloaded_hil` policy and control enabled. Prepare, re-read, obtain fresh human
confirmation that no dispenser or unapproved load is connected and that any
present metrology wiring is operator-approved, enable at zero current, re-read,
compare-and-set one exact 0.2 A commanded-load step, re-read, decrease to zero,
shutdown, and perform a final read. This is supervised interface acceptance only;
it does not validate dispenser conditioning behavior or activation.

The protected state path must be bound before startup and diagnostics must show
`unlatched`. This v0.4.3 interlock has not yet been exercised on physical
hardware; fresh human connection confirmation and a separate reviewed test plan
are required before another live actuation.
