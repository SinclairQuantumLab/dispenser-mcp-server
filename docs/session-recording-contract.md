# MCP-owned session recording — unreleased interface extension

The instrument MCP now owns observation, control and stated-decision records,
CSV projections, and a passive dashboard. This extends the earlier v0.4.3 tool
input contract; it is not a package-version bump or a new hardware-safety model.
The existing structured pressure, power and action results remain unchanged.

## Normal actions

`prepare_dispenser_power`, `enable_dispenser_output`, and
`set_dispenser_current` require an `action_context` object in addition to their
existing arguments. Both context-specific physical enable confirmations remain
required and non-interchangeable.

```json
{
  "session_id": "server-issued-session-id",
  "decision_at": "2026-09-05T12:00:00Z",
  "action": "Prepare the bound supply",
  "background": "The preceding read showed the intended instrument and topology.",
  "rationale_summary": "Prepare the zero-current state before requesting human enable confirmation.",
  "observation_ids": ["server-issued-observation-id"],
  "confidence": {"claim": "Preparation is the intended next action", "value": 0.8}
}
```

This is the agent's brief declared context, not hidden chain-of-thought. The
server checks types, completeness, timezone-aware string timestamps, bounded
text, and references to this session's successfully recorded observations.
An empty observation list is allowed when none is available. Confidence is a
self-reported value in [0,1] or null for unknown, attached to a named claim; it
is not a calibrated probability of activation or a safety-approval threshold.
There is no narrative-quality judge, required reason taxonomy, or decision
engine. The actual tool name and arguments remain the authoritative requested
action; the agent supplies its intent in prose.

A rejected/missing context returns an error with `execution=not_executed` before
the controller is invoked. Ordinary decision/intent recording failure also
prevents dispatch of a normal action and is reported explicitly. Existing
current/step/compliance/identity/wiring/HIL checks still govern the controller.

## Declaration and finish without actuation

The seventh tool, `record_conditioning_decision`, accepts `action_context` and
optional `completion`:

```json
{"outcome":"incomplete", "dispenser_response":"unknown"}
```

Outcome values are `complete`, `incomplete`, `aborted`, `unknown`; response values
are `normal`, `abnormal`, `unknown`. With no completion, it records a hold or
other non-actuating decision. It returns the context, completion, and
`hardware_action_performed=false`. Completion is an agent assessment, separate
from the last instrument-confirmed output/current state. It does not perform
shutdown, claim activation, close the connection, or prevent a later read.

## Shutdown and failure semantics

`shutdown_dispenser_power` remains callable with `{}`. Its hardware operation
is attempted **before ordinary session recording**. Context accidentally
supplied with shutdown is ignored, not validated. Ordinary logger failure
cannot block the attempt or convert a successful shutdown into a hardware
failure. Its intent event is explicitly marked as recorded after dispatch.

This does not bypass the existing controller's startup control-enable and target-identity restrictions. The process-local no-load stop does not block shutdown. The
tool remains software shutdown, not a universally available physical E-stop.

After any successful action, failed post-call recording leaves the original
successful result intact and adds a degraded-recording warning. Never blindly
repeat a control call to repair a log. An error after domain dispatch is marked
`failed_or_unknown`, including when the domain rejected it before a write;
`not_executed` is reserved for failures known to precede domain dispatch.

## Result IDs and clocks

All tool results include `_meta.dispenser_conditioning` with:

```text
session_id, call_id, event_id, observation_id, decision_id,
received_at, recorded_at, recording_status, execution, warning
```

IDs unavailable after a recording failure are null. `observation_id` equals
the result-event ID for a successfully recorded pressure/power/action-state
observation, otherwise null. `recording_status` is `recorded` or `degraded`;
`execution` is `not_executed`, `completed`, or `failed_or_unknown`. A text block
also provides IDs/status so callers can obtain references even if a host hides
MCP metadata. The existing `structuredContent` schemas are preserved.

`decision_at` is supplied by the agent. `received_at` is the server's UTC call
receipt time; `recorded_at` is the raw record write time; `observed_at` remains
the instrument/simulator observation time. They are not interchangeable. For a
declared synthetic source-time origin, plots can position intents by agent
decision time while keeping it distinct from actual observation time. Missing
readings remain missing, and measured native CH1 current is never doubled to
invent a total parallel-load measurement.

## Storage and dashboard

The timestamp is UTC and the random suffix separates concurrent starts. The
folder name does not change session IDs or result schemas. See
[run contents and replay](../runs/README.md). Explicit simulator directory
overrides remain supported; existing historical records are not moved automatically.

Both MCP entrypoints use the shared `new_run_directory` helper. Each process
creates a flat `runs/YYYY-MM-DDTHH-MM-SSZ_<live|simulation>_<8hex>/` directory inside
this source checkout. It contains `metadata.json`, canonical append-only
`events.jsonl`, and derived `observations.csv`, `controls.csv`, `decisions.csv`.
The raw record preserves submitted context and semantic request/results;
transport record identifiers are in the event envelope. CSV appends process
only each new event. To repair derived files after stopping the writer:

```sh
uv run python -m dispenser_conditioning_mcp.session_records rebuild /absolute/session/directory
```

The server assigns a fresh process-scoped session ID; it is independent of the
MCP transport connection. Restart does not resume an energized experiment or
clear the separate HIL state. There is no database, proxy, historian, scheduler,
automatic sampler, retry service, or durable experimental orchestrator.

The same HTTP process serves `/mcp`, `/dashboard`, and the dashboard's read-only
assets/data endpoint. MCP browser-Origin rejection applies only to `/mcp`;
the browser dashboard does not call MCP. It displays last observation time/age,
not an implication of fresh measurement while the agent is idle. Decision and
receipt times, claim-specific confidence, completion and actual returned power
state remain distinguishable. The optional human-only simulation panel below is separate from all MCP results.

Plotly.js basic 4.0.0 is bundled locally under MIT; no runtime CDN request:
https://cdn.plot.ly/plotly-basic-4.0.0.min.js and
https://raw.githubusercontent.com/plotly/plotly.js/v4.0.0/LICENSE.
The full license is in `src/dispenser_conditioning_mcp/dashboard_assets/vendor/`.

To inspect existing recorded data without any hardware configuration:

```sh
uv run python tools/serve_recording_preview.py --session-dir /absolute/session/directory --port 8767
```

This preview command serves only the passive dashboard. The normal instrument
entry point cohosts the same page and records direct MCP calls automatically;
no agent-side recording wrapper is required. All runtime code and assets are
inside this independently cloneable MCP repository.

### Human-readable source and record navigation

The top strip says **SIMULATION**, **LIVE HARDWARE**, or **FIXTURE / SOURCE UNKNOWN**
from session metadata. Hardware source never implies output ON, fresh measurement,
or active execution. Poll/update and observation age are separate indicators.
The caller (agent, script, or human) requests actions; MCP returns results.
Completed is tool completion, not conditioning success. Not executed and error /
state uncertain remain distinct. Short record numbers follow accepted event append
order without rewriting logs; current and voltage points from one power snapshot
share the same number. Click points or supporting-observation links for readable
record details and related decisions/requests/results; original JSON is collapsed.

For simulated sessions only, `/api/simulation-state` reads an explicitly associated
observer dataset for the human-only "Inside the simulated dispenser" panel.
It defaults to `observer.jsonl` in the selected session directory. The preview
accepts optional operator `--observer-file PATH`; this is not a tool argument.
The reader checks available association metadata and never merges distinct runs.
Live or unknown sessions expose no observer state. This endpoint uses the operator dashboard authentication described below;
a remote tool-only decision agent receives no dashboard credentials.
MCP tools, results, context validation, and observation IDs do not include this data.
Remaining stock shares a 0–100% axis. Inventory and release amounts are synthetic
effective units, not mg; loading ratio is not measured composition. Model chamber
pressure and its components are not noisy gauge observations. Both panels are
passive file views; neither advances simulation time or samples hardware.

### Selecting stored runs

The same dashboard's **View run** picker lists the configured directory plus
immediate folders under the shared MCP `runs/` root. **Refresh run list** updates
that list. Compatible runs require readable session metadata and events.jsonl;
legacy folders remain visible with an unavailable reason, without conversion.
`/api/runs` lists options; `/api/session?run=<folder-name>` and
`/api/simulation-state?run=<folder-name>` read only the selected run. Empty selection
always means the configured directory, including previews outside runs/.

Selection is a normal navigation to `/dashboard?run=<folder-name>`, so reload/share
preserves it and the previous page's pending responses, cursors, record numbers,
selected details, and plots are discarded together. Different browsers can inspect
different runs without changing recording or each other. No hardware call occurs.
The **LIVE VIEW / SAVED RUN** indicator describes the view; **LIVE HARDWARE /
SIMULATION** independently describes the recorded data source. A replay-only preview
has no live view even when showing a saved hardware-source run. It does not start,
stop, resume, animate, convert, or modify runs. The plots and human-only simulation
panel remain the same read-only views for selected compatible records.

## Operator dashboard access

Dashboard pages, assets, run listing, recorded observations/decisions, and internal
simulation state share one operator-only route guard. Remote unauthenticated API
requests receive HTTP 401; the main page directs users to `/dashboard/login`.
The login form itself is public but contains no records or code. `/mcp` is separate:
its protocol, public results, control checks and lack of MCP caller authentication
are unchanged. A dashboard login is not hardware-control authorization.

Each HTTP app starts with a random in-memory access code and separate cookie secret.
The phrase appears in the startup operator terminal, at `/dashboard/operator`
for an actual socket-loopback peer, and prominently on the dashboard for loopback
or already-authenticated remote viewers. Anonymous remote/login pages never show
it. Server-side rendering inserts it after access checks, with no-store caching;
static assets, run records and browser storage do not contain it. Remote login exchanges
the submitted code for an HttpOnly, SameSite=Strict session cookie. The code is
reusable during that process, **not a single-redemption OTP**. Restart invalidates
both old codes and cookies. There are no accounts, files, database or token query
parameters. Cookies cover the shared origin's dashboard/API paths but only dashboard
route guards interpret them; the MCP route ignores them. Secure cookies are used
when served over HTTPS; this pilot's direct HTTP traffic is not encrypted.

Owned HTTP entrypoints disable Uvicorn proxy-header trust. Loopback bypass uses the
actual socket peer, never Host, Forwarded or X-Forwarded-For. Do not put an untrusted
remote-facing proxy/tunnel in front of a loopback-bypass endpoint and assume this
preserves isolation: its connections can appear local. The operator must keep the
access code, authenticated browser context, and startup terminal away from the
remote conditioning agent. Do not copy codes into reports, logs, URLs or tool input.

This boundary excludes a remote tool-only agent without credentials. It **does not**
isolate a same-host full-access agent that can read runs, access loopback, inspect
process state or read the terminal. Serious blind simulation additionally needs
an execution environment without those channels. No OS sandbox, TLS deployment,
MCP authentication, proxy service, or agent isolation policy is created here.
The saved-recording preview uses the identical guard and local operator flow.

## Internal simulation backend

`backend = "simulation"` selects the canonical in-checkout Python simulator before
hardware settings or adapters are loaded. The same HTTP process and access guard
serve `/mcp` and the dashboard. The direct backend delegates once to the existing
seven-tool RecordingAdapter: no proxy port, duplicate recorder or second IDs.
`[simulation]` requires operator seed/scenario, and optionally accepts strict
control_enabled (default true) and compliance_voltage_v (default1.0 V, synthetic
test setting only). No hidden scenario/seed is returned by MCP. Hardware backend is the
default and retains its required identity/configuration/credential behavior.
The model's original dynamics/clock and standalone stdio defaults are unchanged.

Pilot session metadata/events and simulator observer snapshots do not emit a
schema_version tag. Readers keep content and session/run association checks.
Development uses one coordinated current format.
there is no legacy migration or normalization. Historical files stay untouched,
but readability across development format changes is not promised. Existing historical/operational files are untouched;
the active no-load latch is process-local.
