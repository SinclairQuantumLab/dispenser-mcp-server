# Dispenser Conditioning MCP

This Python 3.13 MCP server exposes a bounded interface over default stdio or a
reviewed loopback-only Streamable HTTP deployment for:

- one read-only Pfeiffer HiCube Neo G1 total-pressure observation; and
- one operator-bound Siglent SPD3000 dispenser-power topology.

Pressure is **total gauge pressure**, not rubidium partial pressure. Neither a
pressure trace nor a power-supply readback verifies dispenser activation. This
server deliberately stops short of autonomous conditioning orchestration.

See the exact [pressure contract](docs/pressure-observation-contract.md),
[power-control contract](docs/power-control-contract.md), and
[transport/deployment contract](docs/transport-deployment-contract.md), plus the
[verification report](docs/verification-report.md).

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

Every target is bounded by the lower of the operator ceiling, the topology
ceiling, and the hard workflow ceiling of 4.8 A. Under `parallel_ch1`, every
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
the controller commits a pending-operation marker to the protected startup-bound
state file. After every completed mutating power operation, it performs a
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

Only a safely completed operation with a fresh in-band measurement supersedes
its pending marker with a completed-operation record. A crash, unfinished call,
uncertain write, trip-record persistence failure, or completion-record failure
leaves durable fail-closed state. After restart, every mutation is rejected before
a device session opens. Trip recovery commands and verifies the two-channel
shutdown before attempting to replace the already-durable pending marker with a
trip record, so hardware shutdown does not wait on trip-record persistence.

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
uv sync --all-groups
uv run ruff check .
uv run pyright
uv run pytest
```

The canonical Siglent driver is the Git submodule at
`dependencies/py-siglent-spd3000`, pinned by this repository's gitlink. `uv
sync` installs it as an editable local path dependency together with all of its
declared dependencies. The MCP still imports the exact submodule `src` path
selected in operator configuration; no driver wheel or separate editable-install
command is needed during research and development.

The canonical HiCube integration is the byte-for-byte vendored
`dependencies/hicube/hicube_neo_client.py`. Its upstream commit and exact hash
are recorded in `dependencies/hicube/PROVENANCE.md`; no separate HiCube checkout
is required for this development workflow.

All automated tests use injected fakes. They do not contact hardware, resolve
configured device hosts, scan a network, read credentials, or run the Siglent
driver's hardware-marked acceptance tests.

After setting the operator environment below with control disabled, open the
interactive MCP Inspector with:

```powershell
uv run mcp dev src/dispenser_conditioning_mcp/app.py:mcp
```

The release audit also uses Inspector 2.5.0 CLI `tools/list --strict` through an
operator-local config; see [the verification report](docs/verification-report.md).

## Operator startup configuration

The process must receive both integration settings and an explicit safety
policy. Missing or invalid required values deny startup. `control_enabled=false`
allows state reads while rejecting every power write before a driver session is
opened.

Package 0.5.1 changes only startup transport/deployment support and repairs the
dedicated-host dependency, base-runtime provenance, ACL, and owner installation
boundaries. Its independently authenticated Python-payload manifest is narrowly
scoped to the dependency lock, runtime inventory, MCP wheel, and exact
wheelhouse. Other deployment tools, external integrations, configuration, and
the base runtime retain separate operator-owned hash/provenance approvals. The
six tools, their schemas, structured results, literals, and power semantics
remain the public tool contract documented as v0.4.3 for simulator and assembler
compatibility.

| Variable | Required | Contract |
| --- | --- | --- |
| `DISPENSER_MCP_TRANSPORT` | no | Exact `stdio` or `streamable-http`; omitted means backward-compatible stdio |
| `DISPENSER_MCP_HTTP_BIND_HOST` | HTTP only | Optional explicit loopback host; default `127.0.0.1` |
| `DISPENSER_MCP_HTTP_PORT` | HTTP only | Optional 1024–65535; default `8000` |
| `DISPENSER_MCP_HTTP_PATH` | HTTP only | Optional fixed path; default `/mcp` |
| `DISPENSER_MCP_HTTP_TRUST_MODE` | HTTP only | `loopback_only`, `authenticated_ssh_tunnel`, or `authenticated_reverse_proxy` |
| `DISPENSER_MCP_HTTP_ALLOWED_HOSTS` | reverse proxy only | Comma-separated exact Host values; no wildcard |
| `DISPENSER_MCP_HTTP_ALLOWED_ORIGINS` | reverse proxy only | Optional comma-separated exact HTTPS origins; no wildcard |
| `DISPENSER_HICUBE_CLIENT_FILE` | no in this development checkout; yes for other layouts | Absolute commissioned client path; omission uses `dependencies/hicube/hicube_neo_client.py` |
| `DISPENSER_HICUBE_HOST` | yes | Bare configured host/IP; no URL, CIDR, or embedded port |
| `DISPENSER_HICUBE_PORT` | no | OPC UA port, default `4840` |
| `DISPENSER_HICUBE_TIMEOUT_S` | no | `0.1`–`60`, default `5.0` |
| `DISPENSER_SIGLENT_DRIVER_SRC` | yes | Absolute `dependencies/py-siglent-spd3000/src` directory containing `siglent_spd3000/__init__.py` |
| `DISPENSER_SIGLENT_CONNECTION` | yes | Exact fixed value `gateway` |
| `DISPENSER_SIGLENT_IDENTIFIER` | yes | Operator-configured authenticated gateway resource |
| `DISPENSER_SIGLENT_GATEWAY_AUTH_FILE` | no in this development checkout; yes for other layouts | Absolute untracked auth path; omission uses `settings/py-siglent-spd3000-gateway-auth.toml` |
| `DISPENSER_SIGLENT_TIMEOUT_S` | no | `0.1`–`60`, default `5.0` |
| `DISPENSER_SIGLENT_MIN_COMMAND_INTERVAL_MS` | no | `10`–`100`, default `100` |
| `DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT` | yes | `production_dispenser` or `unloaded_hil`; selects the one enable confirmation schema |
| `DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE` | for `unloaded_hil` only | Absolute protected JSON operation/trip state path; rejected for production context and never exposed to MCP |
| `DISPENSER_SIGLENT_TOPOLOGY` | yes | Exact fixed value `parallel_ch1` |
| `DISPENSER_SIGLENT_CHANNEL` | yes | Exact fixed value `CH1` |
| `DISPENSER_SIGLENT_EXPECTED_MODEL` | yes | `SPD3303X`, `SPD3303X-E`, or `SPD3303C`; C is denied for unverified parallel use |
| `DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER` | yes | Exact fresh `*IDN?` serial expected before any write |
| `DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V` | yes | Fixed `0`–`32` V value aligned to expected-model resolution |
| `DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A` | yes | Positive operator ceiling; at most 4.8 A for `parallel_ch1` |
| `DISPENSER_SIGLENT_UPWARD_STEP_A` | yes | Exact fixed load-current step `0.2` A |
| `DISPENSER_SIGLENT_CONTROL_ENABLED` | yes | Exact `true` or `false` |

Gateway connections require a separate local authentication file. For this
development checkout, copy
`settings/py-siglent-spd3000-gateway-auth.toml.template` to the same name without
`.template`; the populated file is ignored. An explicit absolute
`DISPENSER_SIGLENT_GATEWAY_AUTH_FILE` overrides that default for deployed
layouts. The server
uses the driver's strict TOML loader and passes the token only to the gateway
client constructor. Do not put the token into MCP tool arguments, environment
variables, logs, committed files, or Codex-visible configuration.
The file follows the upstream `gateway-auth.toml.template` exactly:

```toml
token = "<non-empty pre-shared token>"
```

No other root key is accepted. The MCP deliberately delegates parsing and
validation to `siglent_spd3000.load_gateway_auth(..., required=True)` so its
authentication contract cannot drift from the gateway implementation.
Direct socket, VXI-11, and VISA connections are denied by this deployment's
startup policy.

Version 0.5.1 retains the v0.4.3 compatibility behavior that accepts the former
`DISPENSER_SIGLENT_UNLOADED_HIL_TRIP_LATCH_FILE` name only as a compatibility
alias for existing operator deployments. The preferred name above describes the
file's operation-marker and trip-record responsibilities. Setting both names is
rejected.

Use [.env.example](.env.example) as a name reference, not as a populated file.
Do not commit endpoints, serial numbers, compliance values, or credentials.
Starting the server validates local files and policy but performs no device
connection. A corresponding tool call opens one bounded session.

```powershell
$env:DISPENSER_HICUBE_CLIENT_FILE = (Resolve-Path "dependencies\hicube\hicube_neo_client.py").Path
$env:DISPENSER_HICUBE_HOST = "<configured-host>"
$env:DISPENSER_SIGLENT_DRIVER_SRC = (Resolve-Path "dependencies\py-siglent-spd3000\src").Path
$env:DISPENSER_SIGLENT_CONNECTION = "gateway"
$env:DISPENSER_SIGLENT_IDENTIFIER = "<configured-resource>"
$env:DISPENSER_SIGLENT_GATEWAY_AUTH_FILE = (Resolve-Path "settings\py-siglent-spd3000-gateway-auth.toml").Path
$env:DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT = "production_dispenser"
$env:DISPENSER_SIGLENT_TOPOLOGY = "parallel_ch1"
$env:DISPENSER_SIGLENT_CHANNEL = "CH1"
$env:DISPENSER_SIGLENT_EXPECTED_MODEL = "<verified-model>"
$env:DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER = "<verified-serial>"
$env:DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V = "<approved-voltage>"
$env:DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A = "4.8"
$env:DISPENSER_SIGLENT_UPWARD_STEP_A = "0.2"
$env:DISPENSER_SIGLENT_CONTROL_ENABLED = "false"
uv run dispenser-conditioning-mcp
```

Omitted `DISPENSER_MCP_TRANSPORT` remains backward-compatible stdio. Version
0.5.1 also supports native Streamable HTTP through startup-only settings. HTTP
always binds to loopback and requires explicit control policy. A control-enabled
HTTP process starts only when the operator binds an
`authenticated_ssh_tunnel` or `authenticated_reverse_proxy` deployment
boundary. These values are assertions about infrastructure outside this
process, not authentication mechanisms by themselves. See the
[transport contract](docs/transport-deployment-contract.md) and
[current Raspberry Pi development workflow](deployment/raspberrypi/QUICK_COMMISSIONING.md).
The [hardened Raspberry Pi deployment note](deployment/raspberrypi/README.md)
and [dedicated Windows deployment note](deployment/windows/README.md) are future
deployment references, not the current research workflow.

For current research, populate `DISPENSER_SIGLENT_DRIVER_SRC` from the pinned
submodule's `src` directory. Startup verifies that imports resolve from that
operator-selected root and that the public gateway API required by this MCP is
present. Git commit pinning is owned by the parent repository's submodule
gitlink, not by a model-facing setting or a runtime wheel-provenance mechanism.

The driver has been exercised on the physical supply through its authenticated
gateway, including concurrent multi-client batches. The MCP stdio read path and
one supervised unloaded-HIL actuation sequence were also exercised on the
unloaded physical supply at 1.0 V compliance and at most 0.2 A commanded load
current. Connected-dispenser actuation remains unvalidated. The durable
measured-current interlock was added after that HIL sequence and v0.4.3 remains
validated only with offline fakes; no further live actuation is authorized
without fresh human connection confirmation and review of this interlock.

### Minimal unloaded-HIL acceptance sequence

For a supply with no dispenser or unapproved load connected, use a low 1.0 V
fixed compliance voltage and lower the operator load-current ceiling to 0.2 A
for the first acceptance run. Operator-approved metrology wiring may be present.
The deployment hard ceilings remain 2.4 A native and 4.8 A commanded load
current; this lower operator ceiling deliberately permits only the first exact
0.2 A load-current step.

1. Keep outputs off and set parallel tracking mode through an operator-controlled
   out-of-band procedure. MCP intentionally exposes no tracking-mode command.
2. Start with `unloaded_hil`, the exact bound identity, 1.0 V compliance,
   0.2 A operator load-current ceiling, exact 0.2 A upward step, and control
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

Register the same executable and environment through the MCP host (for example,
`codex mcp add ... -- uv --directory <this-directory> run
dispenser-conditioning-mcp`). Inspect the advertised catalog before any live
read and keep `DISPENSER_SIGLENT_CONTROL_ENABLED=false` during catalog/read-only
integration.

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
              stdio or loopback Streamable HTTP
```
