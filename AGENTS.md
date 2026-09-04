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
- Keep device host, port, timeout, and client-file path in operator startup
  configuration. Do not add them to model-facing tool arguments or results.
- Opening a HiCube client, reading one batch snapshot, and closing the client
  must remain one bounded synchronous tool operation.
- Sanitize model-visible errors. Device addresses, local paths, and raw driver
  exceptions may not appear in tool results.
- Pressure is total gauge pressure. It is not rubidium partial pressure and
  does not independently verify dispenser activation or function.
- Require explicit startup binding for power acceptance context, topology,
  channel, identity, compliance voltage, current ceilings, upward-step limit,
  and control enable.
- Accept only the `parallel_ch1` topology and `CH1` native channel in this
  deployment's public startup configuration.
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
- Read gateway authentication only from an operator-selected, untracked
  `gateway-auth.toml` file. Never expose its token in logs, tool arguments,
  results, errors, fixtures, or committed configuration.
- Use `settings/py-siglent-spd3000-gateway-auth.toml` as the canonical local
  development credential path. Track only its sanitized `.template`; keep the
  populated file explicitly ignored. Deployed layouts may override it with the
  absolute startup environment setting.
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
  durable-state provider must commit a pending-operation marker. Supersede it
  with a completed-operation record only after the operation and fresh
  measured-current safe-band check succeed. Any unfinished marker, crash,
  uncertain write, trip persistence failure, or completion failure must deny
  mutation before device access after restart.
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
- Keep stdio as the default transport. Streamable HTTP must bind only to
  loopback, enforce exact Host/Origin policy, and reject control-enabled startup
  unless an operator-owned authenticated SSH tunnel or reverse proxy is
  explicitly bound at startup.
- Treat HTTP trust mode as an operator assertion about a real external security
  boundary, not as authentication implemented by this server. Do not add a
  weak bearer-token scheme. The AI client may receive only the MCP endpoint and
  tools, never process lifecycle, environment, credentials, auth files, durable
  state, logs, or reset authority.
- Deploy unloaded-HIL and production profiles as separate process instances
  with distinct ports, authentication paths, policies, and HIL state. Permit
  only one writer for one physical PSU.
- A dedicated Windows host must install runtime dependencies from a reviewed,
  target-specific marker-free lock and release-manifest-verified wheelhouse with
  index access disabled,
  then compare the exact installed distribution inventory. The complete base
  CPython runtime tree must also match an independently approved manifest that
  co-records the clean-install tree and approved offline installer identity.
  This integrity record does not prove causal installation provenance.
  `uv.lock` or a `python.exe` hash alone is not a deployment proof.
- Initialize a fresh protected deployment root and recursively reject every ACL
  identity, owner, or service-account right outside the documented allow-only
  policy. Protect the root before creating descendants; reject a service user
  that is a direct local Administrators member. Independently verify the
  effective service token and enabled privileges during commissioning. Do not
  reuse a pre-existing root or rely
  on `icacls /grant:r` to remove other explicit access entries.

## Development

- Use Python 3.13 and `uv`.
- Keep device integration, domain normalization, MCP registration, and startup
  transport selection separate and independently testable.
- Tests must use fakes and may not contact hardware, scan a network, or read
  credentials.
- Run `uv run ruff check .`, `uv run pyright`, and `uv run pytest` before
  integration.
- stdout is reserved exclusively for the stdio MCP protocol. Diagnostics go to
  stderr.
