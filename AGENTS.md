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
- Keep `parallel_ch1`, `CH1`, `SPD3303X`, the 2.4 A native ceiling, the 4.8 A
  commanded-load ceiling, and the exact 0.2 A upward step as code/contract
  constants rather than configurable operator values.
- Never change tracking mode. Verify the live mode before preparing, enabling,
  or changing current. Identity mismatch must cause zero writes.
- Treat `parallel_ch1` current as a commanded load-current limit derived from
  twice the CH1 native setpoint. Never synthesize a parallel load measurement.
- For this deployment, enforce a 2.4 A native CH1 ceiling, a 4.8 A commanded
  load-current ceiling, and an exact 0.1 A native/0.2 A load-current upward step.
- For the `production_dispenser` acceptance context, require fresh human
  confirmation of physical parallel CH1 dispenser wiring with
  `confirmed_parallel_ch1` immediately before enabling output.
- For the `unloaded_hil` acceptance context, require fresh human confirmation
  that no dispenser or unapproved load is connected with
  `confirmed_no_dispenser_or_unapproved_load_connected` immediately before
  enabling output. Operator-approved metrology wiring, including a voltmeter or
  no wiring, may be present. Never use this context for a connected dispenser.
- Keep the two context-specific confirmation arguments and literals
  non-interchangeable. Instrument state is not evidence of external wiring or
  no-load state.
- Read gateway authentication only from the fixed, untracked
  `gateway-auth.toml` file. Never expose its token in logs, tool arguments,
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
- Under `unloaded_hil`, perform a separate measured-current query after every
  completed mutating operation, including a valid write-free compare-and-set
  replay. A finite value in the inclusive fixed `[-0.001 A, +0.001 A]` band is
  accepted. One finite sample outside the band, any non-finite value, or any
  read/unavailable error must durably latch, run verified two-channel shutdown,
  and reject later mutations before device access. Do not add averaging or
  energized debounce. Keep the threshold and latch context/path/reset/bypass
  outside MCP inputs.
- Before any unloaded-HIL mutating request opens a device session, the protected
  durable-state provider must commit both the primary pending-operation record
  and its separately durable pending guard. Publish a completed-operation or
  trip record only while the guard remains authoritative, and retire the guard
  only after the safe replacement is durably published and verified. Any
  unfinished marker or guard, crash, uncertain write, trip persistence failure,
  or reported completion failure must deny mutation before device access after
  restart.
- Treat a missing durable HIL state file as not operator-initialized and
  fail-closed. Each host deployment must provide a separately protected,
  out-of-band initializer using atomic no-overwrite creation after physical
  output verification; the MCP process cannot bootstrap or recreate it.
- The protected durable-state provider may begin/complete normal operations and
  record the first trip, but it must have no reset/delete method. Human
  out-of-band reset and future physical-button integration remain outside this
  process.
- On Windows, retry atomic durable-state replacement only for the bounded known
  transient sharing errors. Never retry general permission/path failures. Trip
  persistence retries must occur after hardware shutdown, and exhaustion must
  preserve the prior pending state and fail closed.
- Accept only actual JSON integer/float values for model-facing numeric mutation
  inputs. Never coerce strings or booleans into current values.
- Software shutdown and the unloaded-HIL software latch are not a physical
  emergency stop, watchdog, or guarantee of power removal.
- Always serve Streamable HTTP at the fixed /mcp path. Main settings expose only
  allow_remote_access (strict boolean, default false) and port (default 8000).
  False binds 127.0.0.1; true binds 0.0.0.0. Direct IP and hostname clients work
  without a mandatory SSH tunnel or manual Host/Origin lists. Do not detect LAN
  versus Internet or discover interfaces.
- Reject browser Origin headers for the native-client pilot; retain local Host
  checks and bounded request bodies. Remote exposure is the operator's choice.
- During a live run, the execution agent uses MCP tools. The operator owns
  process administration, credentials, and interlock initialization/reset.
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
- Use Python 3.13 and `uv`.
- Keep device integration, domain normalization, MCP registration, and startup
  HTTP startup separate and independently testable.
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
