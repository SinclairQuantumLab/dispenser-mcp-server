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

One persistent HTTP process starts **idle**. Idle startup opens no instrument,
imports no hardware driver, allocates no run and performs no acquisition. An
authenticated human uses `/console` to save one nonsecret run candidate, perform
an explicit read-only check, and then start a new hardware or simulation run.
A failed check leaves the web server available and creates no empty run.

This source checkout runs on any supported host with Git, `uv`, and Python 3.13.
The main TOML supplies only listener bootstrap defaults (`port` and
`allow_remote_access`) to the idle process. The operator console saves run
preferences atomically in ignored `settings/console-settings.toml`; the existing
HiCube and Siglent TOMLs are used only to suggest initial values. Credentials
remain in the ignored gateway-authentication file and are never returned to the
browser or MCP client.

```sh
git clone --recurse-submodules https://github.com/SinclairQuantumLab/dispenser-mcp-server.git
cd dispenser-mcp-server
uv sync
```

The recursive clone checks out the pinned `py-siglent-spd3000` source, and
`uv sync` uses `.python-version`, `uv.lock`, and the editable local dependency
declared in `pyproject.toml`. If `uv` is missing, follow its
[official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

Optionally copy the existing source settings to provide initial console
suggestions and listener bootstrap values. Missing instrument files do not block
idle startup:

```sh
cp settings/mcp-settings.toml.template.hardware settings/mcp-settings.toml
cp settings/hicube-neo-client-settings.toml.template settings/hicube-neo-client-settings.toml
cp settings/py-siglent-spd3000/gateway-settings.toml.template settings/py-siglent-spd3000/gateway-settings.toml
```

`settings/.gitignore` excludes actual `*.toml` recursively; templates stay tracked.
Runtime paths are unchanged. The first three files are optional suggestion/bootstrap
sources; the console writes only the final file:

```text
settings/mcp-settings.toml
settings/hicube-neo-client-settings.toml
settings/py-siglent-spd3000/gateway-settings.toml
settings/console-settings.toml
```

Copy `settings/py-siglent-spd3000/gateway-auth.toml.template` to
`settings/py-siglent-spd3000/gateway-auth.toml`, restrict it to the operator or
service identity, and set the token only if required by the gateway; otherwise leave
the token line commented. The populated file is
ignored by Git. Start the idle web console with the same command for either
backend:

```sh
uv run dispenser-conditioning-mcp
```

Open `http://<server-host>:<port>/console`, log in with the process-scoped
operator phrase shown on stderr, review/save the candidate, select **Check saved
settings**, then explicitly select **Start new run**. Start never prepares,
enables, zeros, ramps or shuts down the PSU. Keep `control_enabled = false` for
first hardware commissioning. Simulation does not require hardware addresses,
serial identity, driver/auth files or invented placeholder values.

Streamable HTTP always listens at `/mcp`. The default
`allow_remote_access = false` binds `127.0.0.1:8000`. To connect directly
from another computer, set `allow_remote_access = true` and connect to
`http://<Pi-IP>:8000/mcp` (or its resolvable hostname). `port` is a top-level
integer setting. SSH forwarding is optional. This supervised native-client pilot
rejects browser Origin headers on `/mcp` and has no built-in client authentication.
The same process serves the observation dashboard at `/dashboard`. Before the
first run it returns to the operator console. After a run ends (or after restart
when current-format saved runs exist), it remains a replay browser for saved
records and never creates a recording merely because the page was opened.

During first commissioning, start a checked read-only run and call only
`read_server_status`, `read_vacuum_pressure` and
`read_dispenser_power_state`. Instrument mutations require the exact active
`run_binding` returned by status. See the
[detailed Raspberry Pi research guide](deployment/raspberrypi/QUICK_COMMISSIONING.md)
for the supervised commissioning sequence.

### One-time update from tracked settings

Before pulling this settings-template migration, back up your existing `settings/`
directory outside the checkout, including any operator credentials, and keep the
backup private. Git may remove previously tracked clean TOMLs during the pull or
refuse the pull when they contain local edits. Preserve those edits; do not use
reset/checkout to discard them. Restore the needed actual TOMLs from your backup
after updating, or copy templates only for missing files. Future actual settings
are ignored and will no longer be replaced by tracked defaults.

## Independent simulation host

After the same recursive clone and ordinary `uv sync`, start the idle server and
choose **Simulation** in `/console`. Enter a nonempty private seed and a supported
scenario such as `nominal_recovery`, save, check, and start. No hardware identity,
HiCube/Siglent address, driver import or populated gateway-auth file is required
for a simulation candidate. `allow_remote_access` and `port` remain listener
bootstrap values in the main TOML.

```sh
uv run dispenser-conditioning-mcp
```

The simulator runs **inside** this process, not at another externally exposed port.
The clone contains its canonical runtime at `src/dispenser_simulator/`; the parent
project is not needed. One RecordingAdapter creates one run under `runs/`, with
the same public observation/control/history contracts, session IDs, records,
protected human dashboard and
internal observer file. Seed/scenario stay operator-only and out of MCP results.
The example is a disclosed connectivity fixture, not a hidden scientific test.
Before a blind run, choose private operator configuration and keep source/files,
loopback, authenticated dashboard and terminal inaccessible to the remote agent.

The console suggestion uses synthetic `production_dispenser`,
`control_enabled = false`, and `compliance_voltage_v = 1.0` V. Review these
explicitly before saving. The voltage is a simulation-only test value, **not
approved for live equipment**. Existing model
limits remain 6.4 A device ceiling and 0.2 A upward steps. Simulation reads and
prepare/enable/set accept optional `elapsed_s` (0..86400 seconds, default 0).
Each physical interaction irreversibly advances by the greater of that request
and actual monotonic wall time since the previous interaction. The first call
ignores pre-client server idle. Controls evolve the old output state before
applying the new action. Shutdown has no future-delay argument. Decisions and
dashboard views do not advance/reset the clock; their wall time counts at the
next physical interaction. Results include `timing` (requested, wall, actual and
cumulative virtual seconds); domain errors after advancement carry
`_meta.simulation_timing`. Invalid interval/context does not advance the model.
No intermediate gauge samples are invented, and physics equations are unchanged.
The dashboard formats elapsed axes as MM:SS below one hour or HH:MM for longer
runs (hours do not wrap), adding seconds or milliseconds when zoomed closely.
Hover retains precise elapsed seconds. CSV units stay unchanged.
The old developer stdio command retains its original 10.0 V default and explicit
environment overrides. HTTP simulation does not import hardware adapters or read
their settings/authentication. Installed dependency code is not device access.
The legacy deployment-check command is for the hardware backend, not required here.

## Tool surface

`read_server_status({})` is always available and performs no instrument read. It
reports idle/running lifecycle, the opaque active `run_binding`, backend,
authority, sampler freshness/health and registered optional-capability
descriptors without exposing connection settings, credentials, seed or scenario.
The instrument and run-history catalog becomes available after the first
explicit run start. Every agent instrument mutation, including shutdown,
requires the exact current `run_binding`; stop/new-run invalidates it. A same-run
authority or sampling-interval change retains it but cancels old action plans.

| Tool | Input | Purpose |
| --- | --- | --- |
| `read_vacuum_pressure` | none | Read one total-pressure snapshot |
| `read_dispenser_power_state` | none | Read identity, topology, native setpoints/readbacks, output state, and fixed safety limits |
| `prepare_dispenser_power` | `run_binding`, `action_context` | Destructively overwrite the bound state: output off, zero current, then fixed compliance voltage |
| `enable_dispenser_output` | `run_binding`, `action_context` + one startup-context-specific confirmation | Enable only from the verified prepared zero-current state after the required fresh human physical confirmation |
| `set_dispenser_current` | `run_binding`, `action_context`, `target_current_a`, `expected_current_a` | Compare-and-set an absolute commanded load-current limit while output is on |
| `shutdown_dispenser_power` | `run_binding` | Perform an energy-reducing destructive overwrite: required outputs off first, then currents zero |

The **unreleased session-recording interface extension** adds a seventh tool,
`record_conditioning_decision(action_context, completion?)`, for a declared hold
or finish without changing power. Normal prepare/enable/set actions require
brief agent context; shutdown still accepts `{}` and executes before ordinary
logging. Read results provide the session and observation IDs to reference.
See [the exact context/recording contract](docs/session-recording-contract.md).

Optional Learning mode adds four metadata tools:

- `read_learning_mode_status` reports passive-sampler state and the active
  permissions without querying instruments.
- `append_conditioning_run_review` idempotently attaches one bounded report and
  guidance-change record to a current or saved run.
- `read_conditioning_run_reviews` pages review summaries or returns one full
  review selected by `review_id`.
- `read_conditioning_run_notes` pages authenticated human notes.

Every explicitly started run performs periodic pressure and power acquisition on
real wall time, independently of the browser and agent. The console's
`authority_mode` is separate: `agent_control` permits bound agent mutations,
while `human_learning` denies every agent instrument mutation, including
shutdown. Authenticated human controls reuse the same controller,
confirmations, compare-and-set rule, ceilings and interlocks. A human operation
from agent authority requires an explicit operator takeover first. Authority or
interval changes drain in-flight work and never change output. Sampler startup,
shutdown and read errors never actuate hardware.
Simulation Learning uses only real wall time; positive `elapsed_s` on public
pressure/power reads is rejected rather than advancing the synthetic future.

Both backends share the MCP checkout’s [run directory](runs/README.md);
simulator defaults use the `_simulation_` label. Each explicit start creates one
new run; one process holds at most one active run.
The server records raw JSONL and CSVs under `runs/<UTC-date-time>_hardware_<8hex>/`. Open
`http://<server-IP>:8000/dashboard` for observations, control attempts/results,
and declared rationale. A prominent source-mode strip distinguishes simulation,
live-hardware records, and unknown/fixture data. Short record numbers link chart
points to readable requests, results, decisions and supporting observations.
Simulated sessions may additionally show a human-only internal-state panel from
an associated observer file; this never changes MCP tool results or decision inputs.
The phrase has two random words and two digits, changes each HTTP process, and
is reusable during that process. Five incorrect logins block further login
attempts for up to 60 seconds across the process; cookies remain separate random
secrets. The dashboard shows the current phrase near the top to logged-in local and
already-authenticated remote viewers, never anonymous visitors. It is valid until
server restart and is not saved in records or static assets.
Ordinary dashboard pages, assets, run lists and observation/action/decision data
are public without login. Internal simulation state and run-management writes
still require the operator phrase at `/dashboard/login`. The locked internal
section does not poll or interrupt ordinary plots; unlocking preserves the selected
saved run. Each HTTP process generates a new code, shown initially only in its
startup terminal and subsequently on already-authorized dashboard pages. The
`/dashboard/operator` convenience page also requires the existing login cookie;
it never trusts loopback source addresses, Host, or forwarded headers. The code
is reusable until restart, not a
single-use OTP; it is never an MCP argument or result. Keep it and the resulting
browser cookie out of the remote decision agent’s context. This does not isolate
same-host agents with access to run files, process memory, or the terminal. See the
[dashboard access boundary](docs/session-recording-contract.md#operator-dashboard-access).

Use **View run** to switch between the current process and saved runs in `runs/`.
Selection changes only your browser; acquisition and recording continue unchanged.
**Refresh run list** discovers newly saved folders. Legacy folders without the
supported metadata/events files are listed as unavailable, without conversion.
The dashboard samples no devices; it shows observation
age. Completion/normal-response declarations are judgments, distinct from actual
returned output-OFF status. No caller-side recording wrapper is required.

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
load-current factor 2, a 3.2 A native ceiling, a 6.4 A maximum configurable load-current
ceiling, and 6.4 A hardware capability. Startup also binds one explicit
acceptance context: `production_dispenser` or `no_load_test`. The active context
and its required enable confirmation are returned in structured safety limits.

The server never changes tracking mode. It requires live `parallel` mode before
prepare, enable, or current change. A requested load-current limit is divided
by two before the native CH1 current setpoint is written. The returned
`commanded_load_current_limit_a` is therefore a command interpretation, **not a
measured load current**. CH1 and CH2 current/voltage are queried sequentially in
one batch, not simultaneously. `measured_parallel_load_current_a` sums the two
finite measured currents only when the queried mode is parallel; otherwise it is
null. Voltages remain separate. Simulation uses explicitly labelled symmetric
model channels. The CH1-only no-load current check is unchanged.

Every target is bounded by the operator cap, software maximum 6.4 A and the
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
- `no_load_test` advertises only
  `no_load_test_connection_confirmation="confirmed_no_dispenser_or_unapproved_load_connected"`,
  after the human verifies that no dispenser or unapproved load is connected.
  Operator-approved metrology wiring, including a voltmeter or no wiring, is
  allowed by this acceptance context.

The wrong context's field or literal is rejected before a driver session opens.
Instrument tracking mode cannot verify external wiring or no-load state. These
literals are caller attestations, not cryptographic proof of human provenance;
the MCP host must keep a real human approval in the loop for every enable action.
Never use `no_load_test` for a connected dispenser.

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

The listener boots from only `allow_remote_access` and `port` in the main TOML;
when that file is absent it defaults to loopback port 8000. Instrument documents
and credentials are not required until an authenticated operator saves and
checks a hardware candidate. `settings/console-settings.toml` is a strict,
closed, atomically replaced nonsecret document. Unknown keys, wrong types,
placeholders and invalid ranges reject save/check before device access. A
simulation candidate does not require unused hardware identity or address
values. The hardware tools retain their structured results and deterministic
safety semantics.

| File | Operator settings |
| --- | --- |
| `settings/mcp-settings.toml` | Listener bootstrap (`allow_remote_access`, `port`) and optional initial console suggestions from the previously supported source profile |
| `settings/hicube-neo-client-settings.toml` | Optional initial HiCube host/port/timeout suggestions |
| `settings/py-siglent-spd3000/gateway-settings.toml` | Optional initial gateway identifier/timeout/minimum-command-interval suggestions |
| `settings/console-settings.toml` | Saved backend, authority, acquisition, hardware/simulation and safety candidate used by check/start |

`control_enabled` and `allow_remote_access` default to `false`; `port` defaults to
`8000`. Hardware acceptance context, expected serial, compliance voltage,
HiCube host and gateway identifier have no implicit deployment value. The
console validates them only when the selected candidate is hardware.

The following deployment contract is fixed in code and cannot be weakened in a
settings file: authenticated gateway connection, `parallel_ch1`, `CH1`, model
`SPD3303X`, native ceiling 3.2 A, commanded-load ceiling 6.4 A, factor 2, and
exact 0.2 A commanded-load upward step. The HiCube client, Siglent driver source,
settings directory, and authentication path are derived from the source
checkout. No MCP tool or normal operator setting accepts any of those paths.

Gateway token authentication is optional and enforced by the gateway. Copy
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
The file must exist; missing/commented/blank tokens produce `None`, passed unchanged
to the gateway client. Set a token only if the gateway requires it. Invalid TOML/types
still fail; no token is fabricated.
Direct socket, VXI-11, and VISA connections are denied by this deployment's
startup policy.
The authenticated **Check saved settings** action performs the read-only
pressure/PSU check without allocating a run. A failed check does not stop the web
listener. Tool calls open bounded sessions only after explicit start. Network
exposure and hardware control are independent; enabling remote access does not
enable control. See the [transport contract](docs/transport-deployment-contract.md).

When updating an existing checkout, preserve your edited settings before pulling:
tracked TOML changes can conflict with upstream changes. Keep acceptance context,
serial, compliance, control, instrument settings, and the untracked auth file.
Remove `transport` and the entire `[streamable_http]` table; add
`allow_remote_access = false` and `port = 8000` at the top level. Set the
boolean to true when remote clients should connect directly. Old transport keys
are rejected rather than silently ignored.

### Minimal no-load test acceptance sequence

For a supply with no dispenser or unapproved load connected, an operator may
approve a low 1.0 V fixed compliance voltage for the first acceptance run.
Operator-approved metrology wiring may be present. The immutable ceilings remain
3.2 A native and 6.4 A commanded load current; the agent must still execute only
the reviewed first exact 0.2 A load-current step in this acceptance sequence.

1. Keep outputs off and set parallel tracking mode through an operator-controlled
   out-of-band procedure. MCP intentionally exposes no tracking-mode command.
2. Start with `no_load_test`, the exact bound identity, 1.0 V compliance,
   exact 0.2 A upward step, and control
   disabled. Read state and verify identity, output off, `parallel` mode, and an
   `unlatched` interlock.
3. Restart with the same policy and control enabled. Call
   `prepare_dispenser_power`, then re-read state.
4. Ask the human immediately before enable to verify that no dispenser or
   unapproved load is connected and that any present metrology wiring is
   operator-approved. Call `enable_dispenser_output` with a fresh `action_context` and
   `no_load_test_connection_confirmation="confirmed_no_dispenser_or_unapproved_load_connected"`.
5. Re-read state, set commanded load current from 0.0 A to 0.2 A with
   compare-and-set, and re-read state. Every completed mutation must produce a
   separate native current measurement inside the inclusive fixed
   `[-0.001 A, +0.001 A]` band or the process-local latch trips. No load-current draw
   or absence of a load should be inferred from the native measurement.
6. Set commanded load current back to 0.0 A, call
   `shutdown_dispenser_power`, and perform a final read.

This sequence is a supervised interface acceptance test, not dispenser
conditioning or activation evidence.

## Integration and later safety layers

Register `http://<server-IP>:8000/mcp` as a Streamable HTTP endpoint in the MCP
host. Keep the process running and inspect the discovered tool catalog before live reads.
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
### Dashboard time views and reported tokens

All pressure/power, internal-model and token panels share one wall/virtual clock
and Fixed / Rolling / Full X range. Zoom or pan anywhere updates every chart.
Fixed retains the range; Rolling follows new points with the current zoom width;
Full fits all known plotted times. Zoom in Full switches to Fixed; zoom in Rolling
changes its retained width. No new data means no rolling movement. Changing the
clock/run selects Full. Full time range changes X only, never resets Y.

Each numerical panel has Auto-Y for finite points inside the visible X range;
log pressure uses positive values only and hidden traces are excluded. Empty
windows retain the previous Y range. Disable Auto-Y to preserve manual Y zoom.
Inventory defaults to fixed 0–100%; request/result categories remain fixed.
Token points use their existing record time, with usage ID and record number in
hover details. Missing time coordinates are omitted, not fabricated.
New model snapshots record actual UTC recorded_at separately from synthetic
observed_at. Older rows without recorded_at are omitted in wall mode and remain
available in virtual mode; no timestamp reconstruction or data rewrite occurs.

Callers may optionally include `action_context.token_usage` with a unique
`usage_id` and nonnegative integer `total_tokens`. Optional input/output/cached
counts and model identify provider-reported details; missing values are not zero.
Input includes cached tokens and output includes reasoning when reported—do not
add them again. Reuse the same ID when one inference supplies several MCP calls.
The dashboard counts each ID once, keeps the first report on conflicts and warns.
CSV preserves repeated submissions: deduplicate `token_usage_id` when analyzing
usage offline. This is a reported subset, not measured app usage or a billing total.

### Optional Codex caller usage checkpoints

The MCP server/Pi never reads Codex history. Pulling this repository alone does
not collect caller metrics. The standalone `tools/codex_token_usage.py` helper
runs only on the caller computer, against one explicitly selected rollout and
one per-conditioning-run cursor. It does not discover threads or read other
rollouts. Start a fresh baseline before a new run; prior development/thread
usage is excluded. One caller owns the cursor.

Example in a caller script launched from this checkout (adapt the two explicit
local paths; keep the cursor with that caller's local run notes):

```python
from pathlib import Path
from tools.codex_token_usage import CodexUsageCheckpoint

usage = CodexUsageCheckpoint(Path("/explicit/caller/rollout.jsonl"),
                             Path("/explicit/caller/run/usage-cursor.json"))
usage.baseline()  # Once before the run, not before every action.

# For each already-chosen normal action/declaration with action_context:
try:
    supplied = usage.decorate(arguments)
except (OSError, ValueError, KeyError, TypeError) as error:
    print(f"Token accounting unavailable: {error}")  # Caller/operator diagnostic.
    supplied = arguments  # Optional metrics must not prevent an action.
result = await client.call_tool(tool_name, supplied)
meta = (result.meta or {}).get("dispenser_conditioning", {})
batch = supplied.get("action_context", {}).get("token_usage")
if batch and meta.get("recording_status") == "recorded" and meta.get("decision_id"):
    try:
        usage.acknowledge(batch["usage_id"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Token acknowledgement failed: {error}; preserve the action result, do not replay it.")
```

No acknowledgement means the same pending batch/ID is attached at the next
checkpoint, without issuing or retrying any control automatically. A transport
error or degraded recording must not trigger a hardware replay to repair usage.
Call shutdown normally, without decoration. Keep the cursor if reporting fails.

Counts are actual newly reported cumulative-counter deltas since the acknowledged
checkpoint, not lifetime totals or an estimate of this action's cost. Accounting
may arrive after the inference that chose an action; a later action/declaration
can carry that batch. Missing, incomplete or reset counters are unavailable,
not fabricated zero. Cached tokens are already included in input; reasoning is
already included in output. The helper does not infer a model name.

### Request positions on simulated time charts

Real decision/receipt timestamps and virtual observation timestamps are different
clocks. New simulator request/decision records retain the known virtual clock at
receipt, before the call's elapsed-time advancement. For an older request without
that clock, the viewer can place it at the same call's returned time, explicitly
labelled as approximate placement rather than a request-time reading. Records
are not rewritten; actual observations keep their own timestamps.

### Dashboard history loading

The dashboard receives server-prepared display records, at most 200 per response,
not full MCP response envelopes or duplicate CSV tables. It shows the first page
immediately, then fetches history pages sequentially with a browser event-loop
yield between pages. “Loading history” changes to “Caught up” when live polling
takes over (one second between checks). Saved runs use the same paging.

Human-only model history is also paged at 200 snapshots, with fixed parameters
once per response. The reader first confirms the selected single-run association
in bounded scans. No model samples or instrument queries are generated by loading.
Raw events.jsonl and CSV files remain unchanged on the server; the collapsed
inspector shows dashboard record fields, not the original MCP envelope.

A dashboard deadline/network error keeps displayed data and retries. It does not
mean that MCP controls, recording, or the equipment stopped. Paging bounds record
count, not individual string bytes; the browser still retains all loaded points.

### Operator current cap

Top-level `max_load_current_A = 4.8` (capital A) sets the combined **commanded**
load-current ceiling for hardware and HTTP simulation. Valid values are finite
numbers greater than zero and no greater than 6.4 A. It is not a measured
parallel-current value. The software maximum 6.4 A / native 3.2 A / parallel
6.4 A device bounds are separate constraints; the exact 0.2 A upward step remains
unchanged.

The authenticated web console owns this setting. It is fixed for the active run:
changing it requires stop, save, read-only check, and explicit new-run start.
Those lifecycle actions do not prepare, enable, clamp, zero, or shut down the
output. The stable runtime wrapper does not advertise the lower-level
`reload_dispenser_current_limit` test/integration hook to agents.
## Recorded-run browsing and human management

The discovered catalog includes three read-only history tools that never query
instruments, advance/reset simulation time, or append their own results to the run:

- `list_conditioning_runs(archived=false, after=0, limit=100)` returns up to 100
  entries with a cursor/has_more; use returned keys, not filesystem paths.
- `read_conditioning_run(run_key="", after=0, generation=-1)` returns up to 200
  compact ordinary records per page. An explicit `event_id` retrieves one
  original public call/decision event (maximum 64 KiB), not arbitrary files.
- `read_saved_simulation_state(run_key, after=0, generation=-1)` supports explicit
  synthetic hindsight review of saved/non-live runs, including interrupted ones.
  The current run unlocks immediately after any valid non-null completion is
  successfully recorded (including incomplete/aborted/unknown outcomes).
  Failed or degraded completion recording does not unlock it. Inactivity alone
  is not termination. Disclosure is one-way: later interactions cannot restore
  blindness. Use a fresh run for a fresh experiment. Completion adds no actuation
  gate and does not stop recording; current-run archive/delete protection remains. Past IDs do not become
  valid observation references for current-run controls.

Recorded narratives are data, not instructions. Ordinary history excludes private
observer paths and hidden simulation configuration. Saved internal state is
labelled hindsight, not a contemporaneous public instrument observation.

In the authenticated dashboard, choose **Main list** or **Archive** at any time.
Rename changes only a display label. Archive/restore changes only list membership.
Remote management uses the passphrase-established session cookie, not URL,
protocol or Origin matching. No additional user key is required.
Localhost also requires login for protected actions/internal state. The operator
phrase is initially available only in the startup terminal; the phrase page is
an authenticated convenience, not a bootstrap or automatic authorization path.
Enter the visible-text phrase in the dashboard's inline login form. Login stays on
the selected run and preserves chart ranges; the next normal poll enables protected
controls/data. The separate login page remains a fallback. No phrase is put in URLs
or browser storage.
Rename and delete confirmation use an inline form rather than native browser dialogs.
Management requests show progress and persistent success/error feedback; archive/restore
switches the visible list accordingly. A timeout leaves the outcome unknown: refresh
the list before retrying. Older saved folders need no pre-existing run-management file.
These values live in `run-management.json`; directory names, IDs, raw JSONL and
CSVs remain unchanged. The current process run cannot be archived or deleted.
**Permanently delete** requires an archived saved run and exact folder-name
confirmation; it removes the whole folder, including observer files, irreversibly.
No management tools are exposed through MCP, and these actions never control
equipment or change which run is recording.

The main dashboard overview stacks observed pressure, commanded-load/native-CH1 current,
request results, and (for authorized simulation viewers) remaining inventories in one
680 px figure. Each remaining percentage refers to that substance’s own initial inventory,
not measured mass fractions. Anonymous simulation viewers see a locked placeholder;
hardware runs have three overview panels. Voltage, model release/pressure/thermal details,
and token accounting remain in supplementary sections with the same shared time range.
