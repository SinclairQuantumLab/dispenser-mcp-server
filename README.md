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

One HTTP process serves either real instruments or an internal Python simulator.
The operator selects `backend = "hardware"` (default) or `"simulation"` in the main
TOML. There is no fallback between them and no model-facing backend switch.

This Python repository runs on any supported host with Git, `uv`, and Python
3.13. Real-hardware first commissioning is read-only. Keep its `control_enabled = false`, and
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

For the hardware backend, first copy the tracked templates to local runtime files
**only when those files do not already exist**, then edit the copies:

```sh
cp settings/mcp-settings.toml.template.hardware settings/mcp-settings.toml
cp settings/hicube-neo-client-settings.toml.template settings/hicube-neo-client-settings.toml
cp settings/py-siglent-spd3000/gateway-settings.toml.template settings/py-siglent-spd3000/gateway-settings.toml
```

`settings/.gitignore` excludes actual `*.toml` recursively; templates stay tracked.
Runtime paths are unchanged. MCP-owned settings use the current field set without `schema_version`; remove that obsolete key from local settings:

```text
settings/mcp-settings.toml
settings/hicube-neo-client-settings.toml
settings/py-siglent-spd3000/gateway-settings.toml
```

The following configuration and deployment check are for **backend = "hardware"**.
For a hardware-free host, use the simulation instructions below instead; no live
settings or populated gateway-auth file is needed.

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
rejects browser Origin headers on `/mcp` and has no built-in client authentication.
The same process serves the read-only dashboard at `/dashboard`.

During first commissioning, call only `read_vacuum_pressure` and
`read_dispenser_power_state`. See the
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

After the same recursive clone and ordinary `uv sync`, replace only
`settings/mcp-settings.toml` with a copy of `settings/mcp-settings.toml.template.simulation` (preserve any
existing operator profile first). For a fresh simulation host:

```sh
cp settings/mcp-settings.toml.template.simulation settings/mcp-settings.toml
```

Set `allow_remote_access = true` if the decision
agent is on another machine, then run the same command:

```sh
uv run dispenser-conditioning-mcp
```

The simulator runs **inside** this process, not at another externally exposed port.
The clone contains its canonical runtime at `src/dispenser_simulator/`; the parent
project is not needed. One RecordingAdapter creates one run under `runs/`, with
the same eight public tools, session IDs, records, protected human dashboard and
internal observer file. Seed/scenario stay operator-only and out of MCP results.
The example is a disclosed connectivity fixture, not a hidden scientific test.
Before a blind run, choose private operator configuration and keep source/files,
loopback, authenticated dashboard and terminal inaccessible to the remote agent.

New HTTP simulation defaults are synthetic `production_dispenser`,
`control_enabled = true`, `compliance_voltage_v = 1.0` V. The voltage is a
simulation-only test value, **not approved for live equipment**. Existing model
limits remain 4.8 A load ceiling and 0.2 A upward steps. Simulation reads and
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

| Tool | Input | Purpose |
| --- | --- | --- |
| `read_vacuum_pressure` | none | Read one total-pressure snapshot |
| `read_dispenser_power_state` | none | Read identity, topology, native setpoints/readbacks, output state, and fixed safety limits |
| `prepare_dispenser_power` | `action_context` | Destructively overwrite the bound state: output off, zero current, then fixed compliance voltage |
| `enable_dispenser_output` | `action_context` + one startup-context-specific confirmation | Enable only from the verified prepared zero-current state after the required fresh human physical confirmation |
| `set_dispenser_current` | `action_context`, `target_current_a`, `expected_current_a` | Compare-and-set an absolute commanded load-current limit while output is on |
| `shutdown_dispenser_power` | none | Perform an energy-reducing destructive overwrite: required outputs off first, then currents zero |

The **unreleased session-recording interface extension** adds a seventh tool,
`record_conditioning_decision(action_context, completion?)`, for a declared hold
or finish without changing power. Normal prepare/enable/set actions require
brief agent context; shutdown still accepts `{}` and executes before ordinary
logging. Read results provide the session and observation IDs to reference.
See [the exact context/recording contract](docs/session-recording-contract.md).

Both entrypoints share the MCP checkout’s [run directory](runs/README.md);
simulator defaults use the `_simulation_` label. One process creates one run.
The server records raw JSONL and CSVs under `runs/<UTC-date-time>_live_<8hex>/`. Open
`http://<server-IP>:8000/dashboard` for observations, control attempts/results,
and declared rationale. A prominent source-mode strip distinguishes simulation,
live-hardware records, and unknown/fixture data. Short record numbers link chart
points to readable requests, results, decisions and supporting observations.
Simulated sessions may additionally show a human-only internal-state panel from
an associated observer file; this never changes MCP tool results or decision inputs.
The phrase has two random words and two digits, changes each HTTP process, and
is reusable during that process. Five incorrect logins block further login
attempts for up to 60 seconds across the process; cookies remain separate random
secrets. The dashboard shows the current phrase near the top to loopback viewers and
already-authenticated remote viewers, never anonymous visitors. It is valid until
server restart and is not saved in records or static assets.
Remote dashboard visitors must enter the operator access phrase at
`/dashboard/login`. Each HTTP process generates a new code, shown only in its
startup terminal and on the server-loopback `/dashboard/operator` page. Actual
loopback connections can view directly. The code is reusable until restart, not a
single-use OTP; it is never an MCP argument or result. Keep it and the resulting
browser cookie out of the remote decision agent’s context. This does not isolate
same-host agents with local file, loopback, or terminal access. See the
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
load-current factor 2, a 2.4 A native ceiling, a 4.8 A configured load-current
ceiling, and 6.4 A hardware capability. Startup also binds one explicit
acceptance context: `production_dispenser` or `no_load_test`. The active context
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

Package 0.6.1 uses the environment-variable-free operator interface introduced
in 0.6.0, comprising three
strict, closed TOML documents. Missing files, missing required values,
placeholders, unknown keys, wrong TOML types, and invalid ranges deny startup
before any device connection. The six hardware tools retain their structured observation/action results and
hardware safety semantics. The unreleased session-recording extension changes
normal control inputs and adds one declaration tool; it is not identical to the
older v0.4.3 input schema.

| File | Operator settings |
| --- | --- |
| `settings/mcp-settings.toml` | Explicit acceptance context, expected PSU serial, fixed compliance voltage, control flag, allow_remote_access (default false), port (default 8000), |
| `settings/hicube-neo-client-settings.toml` | HiCube host, port (default 4840), and timeout (default 5 s) |
| `settings/py-siglent-spd3000/gateway-settings.toml` | Gateway identifier, timeout (default 5 s), and minimum command interval (default 100 ms) |

`control_enabled` and `allow_remote_access` default to `false`; `port` defaults to
`8000`. The
acceptance context, expected serial, compliance voltage, HiCube host, and
gateway identifier have no implicit deployment value and must be filled in.

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

### Minimal no-load test acceptance sequence

For a supply with no dispenser or unapproved load connected, an operator may
approve a low 1.0 V fixed compliance voltage for the first acceptance run.
Operator-approved metrology wiring may be present. The immutable ceilings remain
2.4 A native and 4.8 A commanded load current; the agent must still execute only
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
host. Keep the process running and inspect the eight-tool catalog before live reads.
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

The pressure/power and internal-model time charts each have an independent Fixed / Rolling / Full time view. Fixed preserves
the visible range; Rolling follows new points using the current zoom/pan width;
Full fits all plotted points. Zooming in Full switches to Fixed, while zooming
in Rolling changes its retained width. No new data means no rolling movement.
Changing the main time axis or run resets its view; Reset selects Full and fits Y.

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

### Operator current cap and explicit reload

Top-level `max_load_current_A = 4.8` (capital A) sets the combined **commanded**
load-current ceiling for hardware and HTTP simulation. Valid values are finite
numbers greater than zero and no greater than 4.8 A; omission defaults to 4.8 at
startup. It is not a measured parallel-current value. The fixed absolute 4.8 A /
native 2.4 A policy and exact 0.2 A upward step remain unchanged.

After the operator edits this key, the agent may call
`reload_dispenser_current_limit()` with **no arguments**. The server reads only
this field from its canonical settings/mcp-settings.toml. No value, path,
decision context, device access, actuation or simulated-time advancement is
required. Missing/invalid/unreadable values leave the old cap unchanged. No other
settings (backend, credentials, control authorization, compliance) are reloaded.

The result gives previous/applied/effective caps, explicitly reports hardware
unchanged, and recommends fresh state inspection after lowering. An already
energized output is neither clamped nor turned off; it may remain above the new
cap. Subsequent targets must be <=cap, including decreases; shutdown remains
available. Enable still requires prepared zero current. Raising within 4.8 A is
allowed only through the operator file. 5.0 A is not allowed.

Initialize instructions advertise the initial cap. State/reload results report
the applied cap; cached input schemas retain the absolute 4.8 A ceiling so a later
valid increase is not rejected by a stale discovery limit. All calls are recorded
through the ordinary session logger. The standalone injected simulator without
an operator settings layout advertises reload but reports it unavailable.
