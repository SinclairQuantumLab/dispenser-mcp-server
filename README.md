# Dispenser Conditioning MCP

This Python 3.13 MCP server exposes a bounded Streamable HTTP interface for:

- one read-only Pfeiffer HiCube Neo G1 total-pressure observation; and
- one operator-bound Siglent SPD3000 dispenser-power topology.

Pressure is **total gauge pressure**, not rubidium partial pressure. Neither a
pressure trace nor a power-supply readback verifies dispenser activation. This
server deliberately stops short of autonomous conditioning orchestration.

See the exact [pressure contract](docs/pressure-observation-contract.md),
[power-control contract](docs/power-control-contract.md), and
[transport/deployment contract](docs/transport-deployment-contract.md), plus the
[verification report](docs/verification-report.md).

## Quick start

This Python repository runs on any supported host with Git, `uv`, and Python
3.13. First commissioning is read-only. Keep `control_enabled = false`, and
never commit the populated gateway-authentication file.

```sh
git clone --recurse-submodules https://github.com/SinclairQuantumLab/dispenser-mcp-server.git
cd dispenser-mcp-server
uv sync
```

The recursive clone checks out the pinned `py-siglent-spd3000` source, and
`uv sync` uses `.python-version`, `uv.lock`, and the editable local dependency
declared in `pyproject.toml`. If `uv` is missing, follow its
[official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

Edit the two nonsecret instrument settings files and the main safety/transport
settings file:

```text
settings/mcp-settings.toml
settings/hicube-neo-client-settings.toml
settings/py-siglent-spd3000/gateway-settings.toml
```

Copy `settings/py-siglent-spd3000/gateway-auth.toml.template` to
`settings/py-siglent-spd3000/gateway-auth.toml`, restrict it to the operator or
service identity, and insert only the gateway token. The populated file is
ignored by Git. Fill every placeholder in the three nonsecret TOMLs and leave
`control_enabled = false` for the first start.

Validate and start with the same commands on every supported host:

```sh
uv run python -m dispenser_conditioning_mcp.deployment_check
uv run dispenser-conditioning-mcp
```

The offline check validates local settings and imports without a device
connection. Its stage codes identify configuration, transport, imports,
authentication-file access, and server assembly failures. `--diagnostic`
includes the exception class. These are operator diagnostics, distinct from
sanitized model-facing tool errors. Ordinary local troubleshooting may inspect
nonsecret settings, paths, endpoints, and logs; never disclose token contents.

Streamable HTTP always listens at `/mcp`. The default
`allow_remote_access = false` binds `127.0.0.1:8000`. To connect directly
from another computer, set `allow_remote_access = true` and connect to
`http://<Pi-IP>:8000/mcp` (or its resolvable hostname). `port` is a top-level
integer setting. SSH forwarding is optional. This supervised native-client pilot
rejects browser Origin headers and has no built-in client authentication.

During first commissioning, call only `read_vacuum_pressure` and
`read_dispenser_power_state`. See the
[detailed Raspberry Pi research guide](deployment/raspberrypi/QUICK_COMMISSIONING.md)
for the supervised commissioning sequence.

## Tool surface

| Tool | Input | Purpose |
| --- | --- | --- |
| `read_vacuum_pressure` | none | Read one total-pressure snapshot |
| `read_dispenser_power_state` | none | Read identity, topology, native setpoints/readbacks, output state, and fixed safety limits |
| `prepare_dispenser_power` | none | Destructively overwrite the bound state: output off, zero current, then fixed compliance voltage |
| `enable_dispenser_output` | one startup-context-specific confirmation | Enable only from the verified prepared zero-current state after the required fresh human physical confirmation |
| `set_dispenser_current` | `target_current_a`, `expected_current_a` | Compare-and-set an absolute commanded load-current limit while output is on |
| `shutdown_dispenser_power` | none | Perform an energy-reducing destructive overwrite: required outputs off first, then currents zero |

All schemas are closed with `additionalProperties: false`. No tool accepts a
host, port, path, resource, transport, channel, topology, identity, compliance
voltage, ceiling, tracking mode, or raw SCPI command.
`target_current_a` and `expected_current_a` accept JSON integer or floating-point
numbers only; strings and booleans are rejected before the controller is called.

All four mutating tools are marked destructive because they overwrite device
state. Prepare and shutdown remain idempotent; enable and live current-setting
are non-idempotent because physical heating accumulates even when an absolute
target is repeated. Current setting itself uses compare-and-set:
`expected_current_a` must match the live commanded load-current limit. A retry
whose target is already live returns without a write only when the requested
expected-to-target transition is valid.

## Deterministic power boundary

Startup accepts exactly one deployment profile: `parallel_ch1` on `CH1`, with
load-current factor 2, a 2.4 A native ceiling, a 4.8 A configured load-current
ceiling, and 6.4 A hardware capability. Startup also binds one explicit
acceptance context: `production_dispenser` or `unloaded_hil`. The active context
and its required enable confirmation are returned in structured safety limits.

The server never changes tracking mode. It requires live `parallel` mode before
prepare, enable, or current change. A requested load-current limit is divided
by two before the native CH1 current setpoint is written. The returned
`commanded_load_current_limit_a` is therefore a command interpretation, **not a
measured load current**. Only the native CH1 current measurement is returned;
parallel load-current measurement semantics remain hardware-unverified.

Every target is bounded by the fixed 4.8 A deployment/workflow ceiling and the
topology hardware ceiling. Under `parallel_ch1`, every
positive increase must equal 0.2 A of commanded load current, which maps to a
0.1 A native CH1 step. Decreases are allowed. Live voltage must still match the
fixed compliance voltage before and
after a current write. Identity mismatch causes no write. Any uncertain
post-write failure triggers one best-effort output-off then zero-current
sequence, with no automatic retry. Under `parallel_ch1`, recovery commands and
verifies both CH1 and CH2 in case the live topology drifted to independent.

`shutdown_dispenser_power` may proceed despite a topology mismatch after exact
identity verification. For `parallel_ch1`, it commands and verifies both
outputs off before commanding and verifying both current setpoints zero. The
structured state remains the normal bound-CH1 view. Shutdown is software only,
not a physical emergency stop, watchdog, output lease, or hardware interlock.

Immediately before `enable_dispenser_output`, the agent must obtain the physical
confirmation selected at startup:

- `production_dispenser` advertises only
  `parallel_connection_confirmation="confirmed_parallel_ch1"`, after the human
  verifies the approved physical parallel CH1 dispenser wiring.
- `unloaded_hil` advertises only
  `unloaded_hil_connection_confirmation="confirmed_no_dispenser_or_unapproved_load_connected"`,
  after the human verifies that no dispenser or unapproved load is connected.
  Operator-approved metrology wiring, including a voltmeter or no wiring, is
  allowed by this acceptance context.

The wrong context's field or literal is rejected before a driver session opens.
Instrument tracking mode cannot verify external wiring or no-load state. These
literals are caller attestations, not cryptographic proof of human provenance;
the MCP host must keep a real human approval in the loop for every enable action.
Never use `unloaded_hil` for a connected dispenser.

`unloaded_hil` also activates deterministic durable operation-state control and
a measured-current trip. Before any mutating request can open a device session,
the controller commits a pending-operation record and a separate pending guard
in the protected startup-bound state directory. After every completed mutating
power operation, it performs a
separate fresh native-CH1 measured-current query. A finite value in the inclusive
fixed band `[-0.001 A, +0.001 A]` is accepted. One finite sample outside that
band trips immediately; there is no energized averaging or debounce. A read
error, unavailable value, or non-finite value also trips fail-closed. The first
observation or unavailable-measurement reason, UTC timestamp, and operation are
recorded in an operator-bound latch. The same tool call then commands and
verifies CH1 and CH2 outputs off before zeroing and verifying both current
setpoints; a fresh measured-current value inside the same band must also be
verified before the error reports confirmed recovery. The tool returns an
explicit trip error, and every later mutating request is rejected before a
device session is opened. This also applies to a valid write-free compare-and-set
replay. `read_dispenser_power_state` remains write-free and exposes `unlatched`,
`latched`, or `unavailable_fail_closed` diagnostics.

Only a safely completed operation with a fresh in-band measurement publishes and
verifies a completed-operation record before retiring its pending guard. A
failure before that durable publication leaves the guard authoritative. If
guard-retirement durability is uncertain after successful publication, a crash
can only restore the guard and make restart more restrictive. A crash,
unfinished call, uncertain write, trip-record persistence failure, or reported
completion-record failure therefore leaves a fresh process fail-closed before
device access. Trip recovery commands and verifies the two-channel shutdown
before attempting to replace the already-durable pending state with a trip
record, so hardware shutdown does not wait on trip-record persistence.

On Windows, atomic state-file replacement retries only transient access, sharing,
or lock conflicts (`WinError 5`, `32`, or `33`) with four fixed delays totaling
75 ms. Other filesystem errors are not retried. A pending-marker retry occurs
before device access; a trip-record retry occurs only after verified two-channel
shutdown and recovery-current measurement. If retries are exhausted, the
original pending record remains and control fails closed.

The `0.001 A` limit is hard-coded and returned as
`safety_limits.unloaded_hil_safe_measured_current_abs_a`; no MCP input can change
it. It matches one SPD3303X current-display/programming resolution increment,
but it is a nuisance-trip suppression policy, not a claim that a current inside
the band proves no physical load. The published SPD3303X readback-current
accuracy is materially wider than one increment. A supervised fresh-sample
characterization remains required before another physical acceptance run.

The durable state file and acceptance context are startup-only settings. No MCP tool,
prompt, or argument can select the file, change context, clear, reset, or bypass
the state. The file backend can begin and safely complete normal operations and
record the first trip; it exposes no reset/delete operation. A human
must perform reset through a separately protected out-of-band procedure after
physically verifying the supply. A future physical emergency-button/reset
service should own that procedure. The MCP process needs create/read access,
but production ACLs and process isolation must deny the execution agent direct
access to the latch directory. This software latch is not a physical E-stop,
watchdog, or guarantee of power removal.

The adapter enables the driver's write verification. Each related semantic
write group and each state snapshot is submitted as a non-interleaved gateway
batch. This prevents another gateway batch from interleaving inside one
submitted batch. It does not exclude a separate authorized writer between the
MCP's precondition snapshot and later write batch; all other clients must remain
read-only during a conditioning run until a workflow-duration lease exists.

## Development setup

From this directory:

```powershell
git pull --ff-only
git submodule update --init --recursive
uv sync
uv run pytest tests/test_config.py tests/test_transport.py tests/test_protocol.py
```

The canonical Siglent driver is the Git submodule at
`dependencies/py-siglent-spd3000`, pinned by this repository's gitlink. `uv
sync` installs it as an editable local path dependency together with all of its
declared dependencies. Runtime loads that fixed submodule `src` directory; no
driver path setting, driver wheel, or separate install command is needed.

The canonical HiCube integration is the byte-for-byte vendored
`dependencies/hicube/hicube_neo_client.py`. Its upstream commit and exact hash
are recorded in `dependencies/hicube/PROVENANCE.md`; no separate HiCube checkout
is required for this development workflow.

All automated tests use injected fakes. They do not contact hardware, resolve
configured device hosts, scan a network, read credentials, or run the Siglent
driver's hardware-marked acceptance tests.

Use focused tests for the changed behavior and Ruff on changed Python files;
run Pyright when an interface change warrants it. Full suites, package builds,
and installation audits are not routine pilot-development gates. Start the HTTP
server with the quick-start command and register its URL in your MCP client.

## Operator startup configuration

Package 0.6.1 uses the environment-variable-free operator interface introduced
in 0.6.0, comprising three
strict, closed TOML documents. Missing files, missing required values,
placeholders, unknown keys, wrong TOML types, and invalid ranges deny startup
before any device connection. The six tools, their schemas, structured results,
literals, and safety semantics remain public tool contract v0.4.3.

| File | Operator settings |
| --- | --- |
| `settings/mcp-settings.toml` | Explicit acceptance context, expected PSU serial, fixed compliance voltage, control flag, allow_remote_access (default false), port (default 8000), and optional protected unloaded-HIL state path |
| `settings/hicube-neo-client-settings.toml` | HiCube host, port (default 4840), and timeout (default 5 s) |
| `settings/py-siglent-spd3000/gateway-settings.toml` | Gateway identifier, timeout (default 5 s), and minimum command interval (default 100 ms) |

`control_enabled` and `allow_remote_access` default to `false`; `port` defaults to
`8000`. The
acceptance context, expected serial, compliance voltage, HiCube host, and
gateway identifier have no implicit deployment value and must be filled in.
When the context is `unloaded_hil`, `unloaded_hil_state_file` must be an absolute
path in an existing operator-protected directory. It is rejected in
`production_dispenser`.

The following deployment contract is fixed in code and cannot be weakened in a
settings file: authenticated gateway connection, `parallel_ch1`, `CH1`, model
`SPD3303X`, native ceiling 2.4 A, commanded-load ceiling 4.8 A, factor 2, and
exact 0.2 A commanded-load upward step. The HiCube client, Siglent driver source,
settings directory, and authentication path are derived from the source
checkout. No MCP tool or normal operator setting accepts any of those paths.

Gateway authentication remains the sole populated secret file. Copy
`settings/py-siglent-spd3000/gateway-auth.toml.template` to
`settings/py-siglent-spd3000/gateway-auth.toml`; the populated file is ignored.
The server uses the driver's strict loader and passes the token only to the
gateway client constructor. Do not put the token into MCP arguments, logs,
committed files, or other settings. Its complete format is:

```toml
token = "<non-empty pre-shared token>"
```

No other root key is accepted. The MCP deliberately delegates parsing and
validation to `siglent_spd3000.load_gateway_auth(..., required=True)` so its
authentication contract cannot drift from the gateway implementation.
Direct socket, VXI-11, and VISA connections are denied by this deployment's
startup policy.
Starting the server validates local files and policy but performs no device
connection. A corresponding tool call opens one bounded session. Network exposure and
hardware control are independent settings; enabling remote access does not
enable control. See the [transport contract](docs/transport-deployment-contract.md).

When updating an existing checkout, preserve your edited settings before pulling:
tracked TOML changes can conflict with upstream changes. Keep acceptance context,
serial, compliance, control, instrument settings, and the untracked auth file.
Remove `transport` and the entire `[streamable_http]` table; add
`allow_remote_access = false` and `port = 8000` at the top level. Set the
boolean to true when remote clients should connect directly. Old transport keys
are rejected rather than silently ignored.

### Minimal unloaded-HIL acceptance sequence

For a supply with no dispenser or unapproved load connected, an operator may
approve a low 1.0 V fixed compliance voltage for the first acceptance run.
Operator-approved metrology wiring may be present. The immutable ceilings remain
2.4 A native and 4.8 A commanded load current; the agent must still execute only
the reviewed first exact 0.2 A load-current step in this acceptance sequence.

1. Keep outputs off and set parallel tracking mode through an operator-controlled
   out-of-band procedure. MCP intentionally exposes no tracking-mode command.
2. Start with `unloaded_hil`, the exact bound identity, 1.0 V compliance,
   exact 0.2 A upward step, and control
   disabled. Bind a protected absolute durable-state file path outside agent
   access. Read state and verify identity, output off, `parallel` mode, and an
   `unlatched` interlock.
3. Restart with the same policy and control enabled. Call
   `prepare_dispenser_power`, then re-read state.
4. Ask the human immediately before enable to verify that no dispenser or
   unapproved load is connected and that any present metrology wiring is
   operator-approved. Call `enable_dispenser_output` with only
   `unloaded_hil_connection_confirmation="confirmed_no_dispenser_or_unapproved_load_connected"`.
5. Re-read state, set commanded load current from 0.0 A to 0.2 A with
   compare-and-set, and re-read state. Every completed mutation must produce a
   separate native current measurement inside the inclusive fixed
   `[-0.001 A, +0.001 A]` band or the durable latch trips. No load-current draw
   or absence of a load should be inferred from the native measurement.
6. Set commanded load current back to 0.0 A, call
   `shutdown_dispenser_power`, and perform a final read.

This sequence is a supervised interface acceptance test, not dispenser
conditioning or activation evidence.

## Integration and later safety layers

Register `http://<server-IP>:8000/mcp` as a Streamable HTTP endpoint in the MCP
host. Keep the process running and inspect the six-tool catalog before live reads.
Use `control_enabled = false` during first catalog/read-only integration.

Before unattended autonomous conditioning, separate higher-level work must add
a persistent safety supervisor/output lease, pressure freshness and trip logic,
auditable run state, a physical hardware interlock/watchdog, and validated
activation-decision policy. This MCP draft does not claim any of those layers.

```text
operator-only startup policy
        |                         |
commissioned HiCube client   hardware-validated Siglent semantic driver
        |                         |
pressure normalization       deterministic topology/current controller
        |                         |
        +---------- strict MCP tools ----------+
                             |
              Streamable HTTP at /mcp
```
