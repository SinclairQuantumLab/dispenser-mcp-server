> Current input extension: normal prepare/enable/set now require action_context,
> and record_conditioning_decision records non-actuating judgments/completion.
> See [session recording contract](session-recording-contract.md). Existing
> structured power results and hardware rules below remain the baseline.

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
no-load test path was physically exercised before the durable interlock was
introduced; the v0.4.3 interlock is separately labeled offline-only in its
structured diagnostics. This interface does not implement a conditioning state
machine, pressure trip, activation inference, persistent output lease, watchdog,
or physical interlock. Ordinary session JSONL/CSV records are now provided by
the separate MCP recording adapter.

## Hidden immutable policy

Startup TOML must bind the exact expected serial, one acceptance context, one
fixed compliance voltage, and an explicit control-enable flag. The expected
model, topology, channel, current ceilings, and upward step are contract
constants. No-load stop memory lasts only for the current process.
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
| `no_load_test` | No dispenser or unapproved load connected; operator-approved metrology wiring may be present | `no_load_test_connection_confirmation` | `confirmed_no_dispenser_or_unapproved_load_connected` |

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
fixed `no_load_test_safe_measured_current_abs_a=0.001` threshold.
The state also includes a read-only `no_load_test_interlock` object. Production
reports `not_applicable`. No-load test reports `unlatched` or `latched`, with
its first trip when present and an explicit offline-only validation status.
The latch lasts only for this process. Reading diagnostics never creates, updates, or clears latch state.

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

No-load test input:

```json
{"no_load_test_connection_confirmation":"confirmed_no_dispenser_or_unapproved_load_connected"}
```

The production literal may be supplied only after asking the human operator
immediately before the call to verify the approved physical parallel CH1
dispenser wiring. The no-load test literal may be supplied only after asking the
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

## No-load test post-operation interlock

No-load tests check fresh finite native current after each completed mutation
against the inclusive ±0.001 A band. An outside-band or unavailable reading
triggers best-effort verified two-channel output OFF, then zero current, and a
process-local stop latch. Subsequent energizing is blocked in that process;
explicit shutdown remains available with operator control authorization and
matching instrument identity. An uncertain shutdown is reported, never assumed
successful or automatically retried.

There is no durable safety file, pending guard, initializer, reset acknowledgement,
or startup inspection gate. A new process starts unlatched. The human beside
the instruments handles between-session inspection and state checks. Ordinary
run records preserve observations and requests, not control authorization.
No software check runs continuously or guarantees OFF after process death.

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

## Minimal no-load test acceptance sequence

The recommended first-run procedure uses an operator-approved 1.0 V fixed
compliance voltage and only one 0.2 A commanded-load step. Parallel tracking mode must be set
out-of-band with outputs off because MCP intentionally exposes no tracking
control.

After a control-disabled identity/topology read, restart with the same
`no_load_test` policy and control enabled. Prepare, re-read, obtain fresh human
confirmation that no dispenser or unapproved load is connected and that any
present metrology wiring is operator-approved, enable at zero current, re-read,
compare-and-set one exact 0.2 A commanded-load step, re-read, decrease to zero,
shutdown, and perform a final read. This is supervised interface acceptance only;
it does not validate dispenser conditioning behavior or activation.

`unlatched`. This v0.4.3 interlock has not yet been exercised on physical
hardware; fresh human connection confirmation and a separate reviewed test plan
are required before another live actuation.
