# Dispenser Conditioning MCP

## Language policy

- Write all user-inspectable Codex task commentary and final responses in
  Korean, including those emitted by subagents.
- Keep internal reasoning and agent-to-agent tool messages, instructions, and
  handoffs in English; the root agent translates relevant results for the user.
- Keep all reusable or distributable MCP artifacts in English, including code,
  comments, tests, schemas, repository documentation, and verification reports.

## Scope

- This component exposes read-only vacuum observations and a bounded Siglent
  SPD3000 dispenser-power control surface to MCP clients.
- Keep the model-facing tools limited to the documented conditioning workflow;
  do not expose raw SCPI, discovery, transport, channel, or topology selection.
- Use the exact vendored `dependencies/hicube/hicube_neo_client.py` as the
  development source of truth for the Pfeiffer PVViewer node map, OPC UA quality
  checks, and snapshot normalization. Preserve its recorded upstream commit and
  SHA-256; update it only by an explicitly reviewed byte-for-byte recopy.

## Safety boundaries

- Never browse OPC UA nodes, scan a network, write an OPC UA Value, or call an
  OPC UA Method.
- Keep device host, port, and timeout in strict operator-owned TOML settings.
  Resolve the vendored client path from the source checkout. Do not add any of
  them to model-facing tool arguments or results.
- Opening a HiCube client, reading one batch snapshot, and closing the client
  must remain one bounded synchronous tool operation.
- Sanitize model-visible errors. Device addresses, local paths, and raw driver
  exceptions may not appear in tool results.
- Pressure is total gauge pressure. It is not rubidium partial pressure and
  does not independently verify dispenser activation or function.
- Require explicit startup binding for power acceptance context, expected
  serial identity, compliance voltage, and control enable. Keep the remaining
  hardware policy fixed below.
- Keep `parallel_ch1`, `CH1`, `SPD3303X`, the 3.2 A native ceiling, the 6.4 A
  commanded-load ceiling, and the exact 0.2 A upward step as code/contract
  constants. Operator max_load_current_A may impose a lower cap.
- Never change tracking mode. Verify the live mode before preparing, enabling,
  or changing current. Identity mismatch must cause zero writes.
- Treat `parallel_ch1` current as a commanded load-current limit derived from
  twice the CH1 native setpoint. Measured parallel current sums separately queried
  finite CH1 and CH2 currents only in verified parallel mode, never twice CH1.
  Queries are sequential, not simultaneous; simulated channels assume symmetry.
- For this deployment, enforce a 3.2 A native CH1 ceiling, a 6.4 A commanded
  load-current ceiling, and an exact 0.1 A native/0.2 A load-current upward step.
- For the `production_dispenser` acceptance context, require fresh human
  confirmation of physical parallel CH1 dispenser wiring with
  `confirmed_parallel_ch1` immediately before enabling output.
- For the `no_load_test` acceptance context, require fresh human confirmation
  that no dispenser or unapproved load is connected with
  `confirmed_no_dispenser_or_unapproved_load_connected` immediately before
  enabling output. Operator-approved metrology wiring, including a voltmeter or
  no wiring, may be present. Never use this context for a connected dispenser.
- Keep the two context-specific confirmation arguments and literals
  non-interchangeable. Instrument state is not evidence of external wiring or
  no-load state.
- Read gateway authentication only from the fixed, untracked
  `gateway-auth.toml` file (which must exist, but token is optional). Pass the
  driver's token or None unchanged; the gateway enforces configured authentication.
  Never expose its token in logs, tool arguments,
  results, errors, fixtures, or committed configuration.
- Use `settings/py-siglent-spd3000/gateway-auth.toml` as the canonical local
  credential path. Track only its sanitized `.template`; keep the populated
  file explicitly ignored. Do not add a normal operator path override.
- During research and development, use the pinned Git submodule at
  `dependencies/py-siglent-spd3000` as the canonical Siglent source. Load its
  `src` directory directly and require the expected public gateway API from that
  exact import origin. Do not substitute an arbitrary checkout or branch.
- Use the semantic driver's verified batch support for related writes and
  snapshots. Gateway batch isolation does not authorize another writer or
  provide a workflow-duration output lease.
- If `parallel_ch1` shutdown or post-write recovery runs after live mode drift,
  command and verify both CH1 and CH2 outputs off before zeroing and verifying
  both native current setpoints.
- Under `no_load_test`, perform a separate measured-current query after every
  completed mutating operation, including a valid write-free compare-and-set
  replay. A finite value in the inclusive fixed `[-0.001 A, +0.001 A]` band is
  accepted. One finite sample outside the band, any non-finite value, or any
  read/unavailable error must latch for this process, run verified two-channel
  shutdown, and reject later energizing before device access. Explicit OFF is allowed. Do not add averaging or
  energized debounce. Keep the threshold and latch context/path/reset/bypass
  outside MCP inputs.
- No-load stop memory is process-local. Do not add durable state, pending guards,
  startup inspection, arming or reset acknowledgement gates. The co-located human
  handles between-session checks. Explicit shutdown bypasses the stop latch,
  but still checks operator control authorization and target identity.

- Accept only actual JSON integer/float values for model-facing numeric mutation
  inputs. Never coerce strings or booleans into current values.
- Software shutdown and the no-load test software latch are not a physical
  emergency stop, watchdog, or guarantee of power removal.
- Always serve Streamable HTTP at the fixed /mcp path. Main settings expose only
  allow_remote_access (strict boolean, default false) and port (default 8000).
  False binds 127.0.0.1; true binds 0.0.0.0. Direct IP and hostname clients work
  without a mandatory SSH tunnel or manual Host/Origin lists. Do not detect LAN
  versus Internet or discover interfaces.
- Reject browser Origin headers only on /mcp for the native-client pilot; retain local Host
  checks and bounded request bodies. Remote exposure is the operator's choice.
- During a live run, the execution agent uses MCP tools. The operator owns
  process administration, credentials, and between-session physical checks.
  Development work may inspect and edit source and use ordinary diagnostics.
- Preserve one writer per physical PSU.
- Historical hardened Windows/Pi deployment material is reference-only and
  imposes no gates on this supervised source-checkout research workflow.

## Development

- Reuse the dedicated GPT-6 Astra server engineer. When replacing an older-model
  engineer, use a GPT-6 Astra successor and obtain a concise predecessor handoff
  covering current state, decisions, changes, checks, and remaining work.
- The coordinating root reviews the engineer's results without rerunning tests.
  Do not add reviewers or subagents unless the user explicitly requests them.
- The MCP engineer owns server/UI implementation and transport/data-retrieval
  correctness, not scientific run-outcome interpretation. The conditioning
  decision specialist owns experimental retrospective interpretation/reporting.
- Use Python 3.13 and `uv`.
- Keep device integration, domain normalization, MCP registration, and startup
  HTTP transport separate and independently testable.
- Tests must use fakes and may not contact hardware, scan a network, or read
  credentials.
- Use focused tests for affected configuration, transport, startup, protocol, and
  deployment-check behavior. Run Ruff only on changed Python files; run Pyright
  when an interface change warrants it. Do not require full suites, wheel builds,
  install audits, additional reviewers, or live checks for routine pilot edits.
- Keep source checkout + uv sync + editable pinned submodule as the active path.
  Do not bump versions or rewrite historical reports unless explicitly requested.
- Distinguish sanitized model-facing tool errors from ordinary operator
  diagnostics: local paths, endpoints, and useful failure context may appear in
  operator logs. Credentials and tokens must never appear in either.
- Preserve labeled historical records; do not present old verification campaigns
  as gates or as evidence for new changes.

## MCP-owned session records

- The persistent HTTP/MCP process starts idle. Idle startup validates only the
  listener bootstrap, imports no hardware driver, reads no instrument and creates
  no run. Authenticated operator check performs bounded read-only candidate checks;
  failure leaves the listener alive and creates no run. Close check connections on
  failure and never actuate as cleanup.

- Default hardware and simulator MCP records to the selected SourceLayout checkout’s `runs/` through
  the shared run-directory helper: UTC timestamp, hardware/simulation label, unique
  suffix. Keep each run flat and preserve explicit simulator path overrides.
  The lightweight coordinator owns at most one explicitly started run. Backend
  switches require explicit stop, save, check and start; none of those lifecycle
  transitions imply OFF or ON. Do not move historical records automatically.

- Keep all runtime recorder, context and dashboard code/assets inside this
  independently cloneable repository. Do not import parent-project modules.
- The MCP records direct calls and brief agent-supplied action context. Every
  explicitly started web run samples pressure and power on real wall time,
  independently of agent/browser presence. It never chooses
  actions, assesses narrative quality, or applies confidence thresholds as an
  actuation-approval algorithm.
- Operator authority is independent of acquisition. `human_learning` denies every
  agent instrument mutation (including shutdown); `agent_control` permits only
  exact active-run-bound mutations through unchanged safety contracts. Sampling
  continues in either authority. Authenticated human controls require explicit
  takeover from agent authority and reuse the same controller. Drain in-flight
  calls before an authority/interval transition; never actuate on lifecycle/error.
  Human notes and bounded run reviews are run-owned metadata. Agent tools may
  read notes/reviews and idempotently append reviews, but may not use metadata
  tools to control equipment, stop acquisition, or expose simulator truth.
- In Learning simulation, public pressure/power reads may not request positive
  `elapsed_s`; only actual wall time advances the model. Dashboard reads never
  advance the model. Preserve one serialized simulator clock/control owner.
- Record per-event `actor` provenance (`agent`, `human`, or
  `learning_sampler`) independently of the run-level source. Unknown external
  setting changes record only their observation interval and detection time;
  never invent intent or an exact change time.
- Keep one ignored `settings/console-settings.toml` for saved nonsecret web run
  preferences. Existing main/instrument TOMLs are listener bootstrap and initial
  suggestion sources, not an alternate runtime dispatch path. Credentials remain
  in the fixed ignored auth file. No agent tool exposes settings, seed/scenario,
  connection values, paths or credentials.
- The stable web MCP wrapper always exposes `read_server_status`. Require its
  opaque active `run_binding` on every agent instrument mutation, including
  shutdown; reject stale/idle/human-owned calls before backend dispatch. Same-run
  authority/interval changes retain the binding; stop/new-run invalidates it.
- Normal prepare/enable/set require action_context; keep shutdown callable with
  no context. Attempt shutdown before ordinary recording. Preserve the existing
  controller identity/control-enable/HIL restrictions; do not conflate the
  ordinary session logger with the removed durable safety state. Normal controls
  require pre-dispatch decision/intent recording; shutdown attempts precede logging.
- Retain semantic structured results. Put record identifiers/status in MCP
  metadata and an agent-visible text block. Post-call logging failure must
  preserve the hardware result and must not suggest repeating a control action.
- Record agent decision time, server receipt time, write time, and source
  observation time distinctly. Completion and normal/abnormal response are
  agent declarations, never substitutes for instrument-confirmed output state.
- Dashboard data reads show observation age. Current-process simulator truth is
  human-only until a validated non-null completion has been fully recorded.
  All valid completion outcomes unlock hindsight immediately and permanently;
  saved/non-live interrupted runs also permit review. No inactivity unlock or
  relock guarantee. Completion does not stop the recorder or add actuation gates. Keep
  synthetic units and hindsight versus live observation unmistakable.
- Ordinary history tools bypass instrument/recording dispatch and expose bounded
  public records with safe metadata, never arbitrary files or observer paths.
- Human dashboard management may rename display labels, archive/restore saved
  runs, and delete only archived non-current contained non-link folders after
  exact-name confirmation. Preserve raw IDs/directories on rename/archive;
  never test deletion on operational or historical datasets.
- Keep dashboard run selection per browser/request. The empty selection always
  refers to the configured current directory; saved selections are immediate
  children of runs/. Never change the active recorder, start/stop acquisition,
  or expose arbitrary browser-selected paths. Saved-only preview has no live view.
- Ordinary dashboard HTML/assets, run lists and recorded observations/actions/
  decisions are public. Guard internal simulation data and management POSTs with
  the existing operator boundary. Locked truth must not block ordinary polling.
  Build the public whole-run overview from exact projected samples and sparse
  action/error markers, then load independent bounded exact pages concurrently.
  Cache one serialized append-aware index per run, parse files off the app event
  loop, merge by stable file-order record number, retry failed ranges, and keep
  protected simulator-truth fetching independent from public history. Bound and
  page the visible history cards without dropping the in-memory exact lookup.
  Preserve selected runs across login. Leave /mcp independent. Show access phrases in the HTTP startup terminal and dashboard/operator pages for
  passphrase-authenticated viewers; never anonymous
  remote/login pages, static assets, MCP results, records or URLs. In addition,
  create one independent process-local bootstrap secret, print it only as the
  fragment of a loopback console URL, remove that fragment before POST redemption,
  and atomically consume it before issuing the existing cookie. It expires after
  one successful redemption or process restart. On GUI hosts, make one best-effort
  browser-open attempt only after Uvicorn reports ready; skip Linux sessions with
  neither DISPLAY nor WAYLAND_DISPLAY. Keep the terminal phrase fallback. Disable
  proxy_headers on owned HTTP entrypoints. This excludes
  uncredentialed tool-only agents; localhost also needs the login cookie for protected
  actions/internal state. Do not use Origin/URL/protocol as additional authorization.
  Never trust socket peer, Host, Forwarded, or X-Forwarded-For for phrase
  disclosure; an opaque same-host proxy can make every client appear local.
  This does not isolate same-host full-access agents. Do not claim OS isolation.
- Operator TOML selects hardware (default) or simulation backend before hardware
  assembly. Never fall back between them or expose backend/seed/scenario in tools.
  Simulation runs directly in-process with one existing RecordingAdapter and one
  recorder; do not wrap it in the hardware recording server. Canonical simulator
  source lives in src/dispenser_simulator under its specialist's ownership. Keep
  physics equations unchanged and sibling developer package a bootstrap only.
  Simulation timing is agent-selected elapsed_s with actual monotonic wall time
  as a floor; controls evolve the prior state first. No fixed per-call ticks.
  Shutdown accepts no requested delay; decisions/dashboard never reset the clock.
- Track sanitized settings/*.toml.template* files (including nested instrument
  templates), never actual operator TOMLs. settings/.gitignore owns the recursive
  *.toml exclusion. Preserve local files when changing Git tracking; back up
  settings before pulling the one-time tracked-to-template migration.
- Pilot session/event/observer records need no single-format schema_version tag.
  Preserve structural/association checks. Use one current format across settings,
  records; do not add legacy dispatch or normalization.
  Update code and development fixtures together. Never clear/reinitialize actual
  existing operational/historical data as part of code edits.
- Remote update workflow: modify locally, push, then root asks the operator to
  pull and restart. Do not add compatibility layers for stale remote code or
  administer the remote host as a substitute for the operator's deployment.
- All dashboard charts share UTC, current-server-local, browser-local, real
  elapsed and (for simulations only) model elapsed clocks plus one
  Fixed/Rolling/Full X range. Real elapsed starts at metadata.created_at, never
  the first page or sample. Use the current host's explicit IANA zone for server
  display and disable that choice when unavailable; never substitute the browser
  zone or claim an original historical acquisition locale. Preserve UTC records.
  Auto-Y is per numerical panel and uses finite visible-time data (positive for log).
  Manual Y ranges survive refresh when Auto-Y is off. Never reconstruct actual
  wall timestamps from synthetic observed_at; omit unavailable wall coordinates.
- Optional token_usage is caller-reported accounting, never automatic app usage,
  billing truth or a conditioning policy input. Preserve missing values; deduplicate
  usage_id per run for display, warn on conflicting repeats, keep raw submissions.
- Operator top-level max_load_current_A is the only reloadable settings exception:
  strict finite 0 < cap <= 6.4 A, startup default 4.8; reload requires the field.
  reload_dispenser_current_limit accepts no arguments/context/path/value, reads
  only canonical main settings, and changes no instrument output or simulation
  time. Invalid reload leaves the previous cap intact. Serialize hardware reload
  and target validation under the existing power lock. Keep all other settings
  startup-only, static absolute schemas, and actual current-cap readback.

- Separate the configurable software maximum 6.4 A from SPD native 3.2 A /
  parallel 6.4 A device capability in current_policy.py. Default operator cap is
  4.8 A; effective is the minimum. Physics reference-power constants are unrelated.
- Keep the dashboard overview focused on pressure, current, requests/results and
  authorized simulation remaining inventories; voltage/model diagnostics/tokens go
  below. Never fetch or retain model traces/details for anonymous viewers, including
  after authorization loss. Inventory percentages use each substance's own initial amount.
