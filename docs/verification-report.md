> Historical reference — retained for provenance, not an active deployment or
> testing gate. Current source-checkout research instructions are in the
> server README and deployment/raspberrypi/QUICK_COMMISSIONING.md. Legacy
> settings, hardened bundles, and commands below may not match the current pilot.

# Verification Report

> **Current R&D startup note:** Package 0.6.1 loads three strict TOML settings
> files from the source checkout and derives the vendored HiCube client,
> parent-pinned Siglent submodule source, and untracked authentication path.
> It installs all declared dependencies through `uv sync`. Environment-profile,
> built-wheel deployment, and release-manifest passages below record earlier
> deployment-hardening work and are not the current research startup path.

Date: 2026-09-05 (America/Chicago)

## Verified build

- Python 3.13 under `uv`
- MCP Python SDK 2.1.1, default stdio plus loopback Streamable HTTP startup
- Package `dispenser-conditioning-mcp` 0.6.1 TOML operator-interface candidate
- Public six-tool MCP contract remains v0.4.3 with identical schemas, literals,
  structured results, and power-safety semantics
- Platform-independent wheel and source distribution
- Authenticated built `py-siglent-spd3000` package with generated commit
  `0984bba67d8e5651cd2d9aa7b2c0db2d6eb694f3`
- Commissioned `hicube_neo_client.py` identity
  `a7bdbf45836f6c92d149f0cdb2dee439d17fcd6b1ce3836404df23fa1c0a4325`

## Commands and results

```text
uv lock --check                  PASS
uv run ruff format --check .    PASS
uv run ruff check .             PASS
uv run pyright                  PASS (0 errors, 0 warnings)
uv run pytest -q                PASS (227 tests)
uv build --offline              PASS (0.6.1 sdist and wheel)
isolated wheel/import smoke     PASS
exact 37-distribution inventory PASS
packed stdio/deployment smoke   PASS (6 tests)
MCP Inspector strict tools/list PASS (production context; 6 tools)
Siglent upstream suite          PASS (142 non-hardware tests)
current-driver contract smoke   PASS (strict auth plus verified semantic batches)
live HiCube MCP read            PASS
live Siglent MCP read           PASS (authenticated stdio tool call)
supervised unloaded-HIL MCP run PASS (completed before durable interlock)
post-publication fsync regression PASS (fresh process remains fail-closed)
```

The durable-state regression injects a parent-directory `fsync` failure after
the completed-operation record has already replaced the primary pending record.
The call reports failure, the separately durable pending guard survives, and a
new controller rejects mutation before its session factory is called. Additional
fault injection covers guard creation, guard-file and directory synchronization,
completed-record staging/replacement/verification, guard retirement, trip
publication, and repeated successful operations. The six-tool public contract
was unchanged by this internal crash-consistency repair. Package 0.6.0 later
changed only the operator configuration interface.

The 0.6.1 TOML tests cover closed schemas, strict TOML types, placeholder and
missing-value rejection, safe stdio/control-disabled defaults, context-bound
absolute unloaded-HIL state, fixed repository source/auth paths, and sanitized
parse errors. A source-checkout stdio process built from generated offline TOML
fixtures advertised six tools and completed both read calls. Inspector strict
`tools/list` used offline identifiers and did not invoke a device tool. The
source distribution contained all four tracked settings/templates and no
populated `gateway-auth.toml`; an isolated Python 3.13 environment imported the
built 0.6.1 wheel successfully. Offline deployment-check tests cover every
stage code, safe `ConfigurationError` visibility, raw-exception redaction,
stdout/stderr and exit status, unknown arguments, and the no-content-read
authentication access check. The offline check now applies the same transport
policy validation as executable startup.

The isolated packed smoke command installed the built 0.5.1 wheel and its
dependencies into an isolated environment, started the packaged stdio entry
point, listed all six tools, and called both read tools against generated
offline HiCube and Siglent fakes. It also exercised the protected-profile
launcher and offline deployment import check against a generated host layout.

Version 0.5.1 retains the deployment transport support. Stdio remains the default.
The new startup configuration accepts only stdio or Streamable HTTP, rejects
deprecated SSE and unknown transports, restricts HTTP bind to loopback, fixes a
256 KiB request-body limit, and enforces exact Host/Origin policy. HTTP startup
requires an explicit power-control setting. A control-enabled HTTP process
rejects `loopback_only` and requires an operator assertion that an authenticated
SSH tunnel or authenticated reverse proxy exists outside this process. The
server implements no weak bearer-token substitute and never permits a direct
non-loopback bind.

Offline ASGI tests exercise the SDK's Host and Origin rejection paths without
opening a listener. MCP Inspector 2.5.0 CLI `tools/list --strict` passed against
both acceptance contexts over offline stdio, advertising the same six public
tools as v0.4.3. Protocol regression tests prove that transport, bind, port,
path, trust, allowed-host/origin, and credential settings do not enter any tool
input schema.

The Windows deployment bundle includes separate control-disabled unloaded-HIL
and production templates, a strict allowlisted launcher, an offline local import
check, exact artifact pinning steps, explicit service-account ACLs, firewall and
SSH/reverse-proxy boundaries, read-only/HIL/production commissioning stages,
restart behavior, and rollback. The MCP wheel does not bundle the commissioned
HiCube client or Siglent driver, so the runbook independently pins, hashes,
installs, and grants service-account RX access to both reviewed source artifacts.
It also grants RX to the installed virtual environment after installation.
The corrected SSH path uses identical local/backend HTTP ports because SSH
preserves the Host header, removes every active inbound allow rule targeting
TCP/22 or `sshd` before installing one source-restricted replacement, and
requires `MaxSessions 0` plus negative shell/subsystem tests. Actual firewall,
OpenSSH syntax, account, and forwarding tests remain commissioning-host gates;
they were not applied to this development host.

The Raspberry Pi deployment is the primary dedicated-host path for package
0.5.1. Its systemd units are control-disabled by profile, loopback-bound,
`Restart=no`, and intentionally not boot-enabled. Separate non-login identities,
profiles, auth files, ports, device identities, and durable state boundaries are
checked mechanically. The HIL durable-state provider now denies a missing state
file; only the stopped-service, root-operated initializer can atomically create
the initial record. POSIX state replacement fsyncs both the file and parent
directory. This remains filesystem crash-durability, not a hardware watchdog or
guarantee that loss of Pi power turns the PSU output off.

The authenticated Pi release manifest rejects undeclared deployment files,
caches, bytecode, the wrong Siglent source layout, a missing generated
`_build_commit.py`, any build commit other than the reviewed commissioned
commit, and any HiCube client other than the reviewed exact hash. The root-only
offline installer requires fresh protected targets and materializes, fsyncs, and
immediately byte-verifies the runtime tools/records plus both instrument source
packages into fixed `/opt` paths before creating the venv. The release process
therefore has no unauthenticated manual source-copy step. Native aarch64 clean
install/import, ELF dependency checks, effective systemd/SSH policy, reboot and
crash behavior, and read-only device commissioning remain blocking checks on the
actual Pi; none was simulated as physical-host proof here.

The staged v0.5.1 repair replaces the earlier non-reproducible wheel install
with a deployment-specific exact-version/hash requirements export, a closed
Windows CPython 3.13 AMD64 Python-payload manifest, index-disabled installation,
and an exact installed-distribution inventory check. The manifest reconstructs
and verifies the dependency lock, inventory, MCP wheel, and exact wheelhouse
filename/hash set. It rejects incompatible platform/Python/ABI tags before
virtual-environment mutation. The deployment lock is Windows CPython 3.13 AMD64
target-specific and marker-free, and its distribution set must exactly equal
the inventory minus the MCP package and the wheelhouse set. The validator also
opens every wheel and rejects unsafe/duplicate members, mismatched dist-info
identity, non-Windows native libraries, and filename/internal-tag drift.
`uv.lock` remains the development lock and is not
claimed as deployment proof. The current development cache could regenerate
and validate the hash-locked export offline, but it could not produce a complete
transferable Windows wheelhouse. Release approval therefore remains blocked
until a controlled Windows release workstation downloads every hash-approved
wheel and publishes the Python-payload manifest hash through an independent
channel. The dedicated host performs no index access.

The base runtime is no longer represented by only a `python.exe` hash. A
separate manifest tool verifies the approved offline installer's SHA-256 and
Authenticode signer and co-records that source identity with every regular file
in a procedurally clean CPython runtime tree. Installation validates the full
tree before using that interpreter. The record does not prove which installer
caused the tree. The expected installer identity, clean-install procedure, and
resulting runtime-manifest hash still require independent operator/reviewer
approval; the manifest is byte-integrity evidence and does not establish
publisher trust by itself.

An offline isolated install of the MCP wheel by itself reproduced the original
dependency-drift defect: the wheel's valid range selected `sse-starlette`
3.4.10 while the reviewed lock/inventory requires 3.4.8, and the inventory
validator rejected it. Installing the 36 exact locked dependencies followed by
the verified MCP wheel with `--no-deps` produced the expected 37-distribution
inventory and passed. The packed stdio test then spawned that installed Python,
advertised six tools, and completed both read operations against offline fakes.

The staged v0.5.1 ACL repair refuses a pre-existing deployment root, rejects a
disabled/non-local service user or a direct local-Administrators member, and
protects the fresh root before creating descendants. It constructs every
policy-directory DACL from scratch with the exact deployment-operator,
Administrators, SYSTEM, and service-user SIDs. The exact operator owns all
protected objects except that service-owned runtime objects are allowed only in
the HIL-state and log trees. The service receives read/execute except for Modify
in those two trees. A recursive blocking validator checks all installed objects
for reparse points, owner drift, protected policy-directory inheritance,
canonical ACLs, duplicate/deny/unapproved ACEs, exact rights, exact inherited
origin/inheritance/propagation flags, and required identities. Root creation and
ACL assignment are not atomic; the documented
parent directory must be operator-controlled, and no descendants are created
during that interval. Adversarial Windows tests prove Administrators-member
rejection before root creation, reproduce and reject a surviving explicit
BUILTIN\\Users ACE and inheritance-flag drift, and validate a fresh recursive
tree including runtime state. The script proves only direct Administrators
membership absence; the runbook separately blocks on a real service-token
groups/privileges review.

The distributed PowerShell launcher is not Authenticode-signed. The corrected
runbook therefore does not claim `AllSigned`; it requires operator SHA-256
verification, protected ACL placement, site-policy review, `Unblock-File` only
after hash approval, and `RemoteSigned`. A real dedicated non-administrator
account launch and ACL review remain commissioning-host acceptance steps; this
development host exercised the same launcher only with an offline fake layout.

The source-distribution target now uses an explicit product allowlist and
excludes `.codex-tmp`, virtual environments, build output, caches, bytecode, and
the local external-driver/auth directory. Archive-entry inspection confirmed
that the final sdist contains the reviewed source, tests, contracts, deployment
bundle, lockfile, and templates without those excluded trees or any
`gateway-auth.toml` file.

An independent Windows release-candidate run found one transient
`PermissionError [WinError 5]` while atomically replacing a valid pending record
with its completed record. Serial reproduction failed on the fourth consecutive
full-suite invocation while the isolated prepare case passed 10/10, which is
consistent with a short external sharing lock rather than an in-process open
handle. The corrected backend retries only Windows access/share/lock errors with
a 75 ms total sleep bound. Injected tests prove transient recovery, bounded
exhaustion with the pending record preserved, and immediate rejection of an
unrelated replace error. After correction, the isolated prepare case passed
10/10 and the complete 106-test suite passed 10/10 consecutive serial runs.

The independent v0.3 audit corrected two contract defects in the preexisting
implementation: non-gateway power connections were still accepted at startup,
and the deployment-specific 2.4 A native ceiling was enforced only indirectly
rather than returned in structured safety limits. It also replaced ambiguous
batch wording with the exact guarantee: each related write group and each state
snapshot is individually non-interleaved, while another authorized client may
still act between submitted batches.

The fresh live HiCube check started the real stdio MCP server with power control
disabled and called `read_vacuum_pressure`. At
`2026-09-04T06:34:35.621328Z`, the result was
`1.4500000133921276e-07 mbar` (`1.0875894499659678e-07 Torr`) from
`pfeiffer_hicube_neo.pvviewer.g1_pressure`, with P1 drive serial `72892052`.
It retained `is_total_gauge_pressure=true`,
`is_rubidium_partial_pressure=false`, and
`verifies_dispenser_activation=false`.

Version 0.4 adds an explicit startup-bound acceptance context. A production
server advertises only the parallel-dispenser confirmation, while an unloaded
HIL server advertises only its connection confirmation. The protocol and domain
layers both reject a confirmation from the other context before device access.

The live Siglent work used the authenticated gateway without printing its
endpoint, token, authentication path, or local source paths. An initial
out-of-band semantic-driver check found CH1 output off but CH2 output on in
`independent` mode. The setup session commanded and verified both outputs off,
set parallel tracking out-of-band, verified `parallel` with both outputs off,
and closed before MCP actuation began.

The supervised `unloaded_hil` MCP stdio run started at
`2026-09-04T06:46:16.294866Z` and completed at
`2026-09-04T06:47:17.756736Z`. It used exact identity binding, 1.0 V fixed
compliance, a 0.2 A operator commanded-load ceiling, and the then-current fresh
human `confirmed_no_load_connected` attestation. That historical literal is not
accepted by v0.4.2. Actual MCP calls completed this sequence:

- control-disabled read: parallel, output off, 1.0 V native voltage setpoint,
  0.1 A native current setpoint, measured 0 V / 0 A / 0 W;
- prepare and read: output off, native current 0 A, commanded load 0 A, measured
  0 V / 0 A / 0 W;
- enable and read at zero current: output on, native current 0 A, with the
  separate read measuring 0 V / 0 A / 0 W;
- compare-and-set 0.0 to 0.2 A commanded load: native setpoint 0.1 A, measured
  0.999 V / 0.001 A / 0.001 W; the separate read measured
  0.998 V / 0.001 A / 0.001 W;
- compare-and-set 0.2 to 0.0 A, then software shutdown; and
- final power read at `2026-09-04T06:47:16.932551Z`: parallel, output off,
  native current setpoint 0 A, commanded load 0 A, 1.0 V compliance setpoint,
  and measured 0 V / 0 A / 0 W.

The mid-run pressure observation at `2026-09-04T06:46:57.289735Z` and final
observation at `2026-09-04T06:47:17.475780Z` were both
`1.4500000133921276e-07 mbar` (`1.0875894499659678e-07 Torr`) with the same G1
total-gauge provenance and explicit non-activation flags. The run never
exceeded 1.0 V compliance or 0.2 A commanded load current. It was an unloaded
interface test, not dispenser conditioning or activation evidence.

Version 0.4.1 was added immediately afterward in response to the observed
nonzero `0.001 A` unloaded readback. Version 0.4.3 retains the v0.4.2 policy that
accepts finite values in
the inclusive fixed `[-0.001 A, +0.001 A]` band and immediately trips on one
sample outside that band. Read errors, unavailable results, and non-finite values
also durably trip fail-closed. Version 0.4.3 additionally establishes a durable
pending-operation record before device-session creation, supersedes it only after
safe completion and a fresh in-band measurement, rejects non-JSON numeric types at
the MCP boundary, and makes legacy/current trip variants structurally strict. This
is a nuisance-trip suppression policy, not proof of no physical load: the
SPD3303X uses 1 mA current resolution while its published readback-current
accuracy is wider. No further live actuation occurred. The v0.4.3 interlock is
therefore validated by offline fault-injection tests only and is explicitly
reported that way in structured state.

## Covered safety and protocol behavior

Tests cover:

- required local gateway authentication file and rejection of every
  non-gateway connection mode;
- token loading through the driver's strict TOML loader without exposing the
  token to MCP arguments, results, errors, fixtures, or committed configuration;
- exact topology/channel/model/serial binding and explicit control enable;
- required `production_dispenser` or `unloaded_hil` acceptance context, with
  mutually exclusive advertised confirmation fields and literals;
- the truthful unloaded-HIL confirmation that excludes a dispenser and
  unapproved loads while allowing operator-approved metrology wiring;
- a 2.4 A native CH1 ceiling, 4.8 A commanded load-current ceiling, and exact
  0.1 A native/0.2 A load-current upward step for `parallel_ch1`;
- strict closed schemas and rejection of unknown arguments before integration;
- strict rejection of numeric strings and booleans before controller/session
  creation, while JSON integer and floating-point numbers retain normal schema
  semantics;
- required context-specific caller attestation for output enable after fresh
  human confirmation of the applicable physical state, with its provenance
  limitation stated explicitly;
- conservative MCP annotations for all heating actions;
- total-pressure/partial-pressure/activation interpretation boundaries;
- parallel factor-2 command translation without a synthesized load-current
  measurement;
- separate driver and MCP hardware-validation status;
- startup-bound protected unloaded-HIL operation/trip state with no model-facing
  path, context, reset, clear, or bypass surface;
- write-ahead pending-operation persistence before any unloaded-HIL mutating
  request opens a device session, safe-completion replacement only after a fresh
  in-band measurement, and restart denial for unfinished operations;
- bounded Windows atomic-replace retry only for transient access/share/lock
  errors, with exhaustion preserving the pending fail-closed record;
- a fresh measured-current query after every completed unloaded-HIL mutation,
  including write-free compare-and-set replay;
- inclusive `-0.001 A`, `0.0 A`, and `+0.001 A` safe-band boundary cases, and
  immediate signed outside-band trip cases without averaging or debounce;
- unavailable, read-error, and positive/negative non-finite measured-current
  fail-closed trips on a write-free replay;
- persistent first-trip records, restart denial before device-session creation,
  unreadable/persistence-failure fail-closed behavior, and write-free structured
  interlock diagnostics;
- compatibility parsing for structurally valid v0.4.1 schema-version-1 records
  without clearing or reinterpreting their latched state, plus fail-closed
  rejection of invalid v1/v2 persisted combinations;
- trip recovery ordering: both outputs off and verified before both current
  setpoints are zeroed and verified, followed by measured-current verification
  inside the same inclusive fixed band;
- control-disabled and identity/topology mismatch rejection before writes;
- non-interleaved verified prepare, enable, current, shutdown, and recovery
  write batches;
- six-query state snapshots submitted as one non-interleaved gateway batch;
- compare-and-set transitions, write-free replay, exact upward step, safe
  decrease, and compliance-voltage verification;
- two-channel parallel shutdown output-off verification before current-zero;
- explicit unknown-output warnings after unverifiable recovery; and
- one driver session close per bounded call.

## Deliberately not exercised

- No live actuation was attempted after adding any durable unloaded-current
  interlock version; v0.4.3 was verified offline only.
- No connected dispenser, heater, or other unapproved load was attached during
  the completed HIL run.
- No production-dispenser actuation was attempted, and no pressure result was
  interpreted as activation evidence.
- The v0.4.3 pending-operation, trip, persistence-failure, restart, and
  uncertain-shutdown branches
  were exercised only with offline injected fakes.
- No token value was printed, logged, copied, or placed in model-visible
  configuration.
- No tracking command, arbitrary SCPI, network configuration, save/recall,
  timer, waveform, or device-lock command is reachable through MCP.
- Inspector exercised catalog discovery only and did not contact either
  instrument.
- Codex host registration was not performed because live actuation policy is
  not yet complete.
- The enable confirmation literal is a caller attestation. The MCP server
  cannot independently prove that a human supplied it immediately before the
  call, so the host must enforce that approval step.

## Required before another live power-control run

1. Review the v0.4.3 interlock and obtain a new human confirmation that no
   dispenser or unapproved load is connected and any present metrology wiring
   is operator-approved; the previous confirmation cannot be reused.
2. Bind a protected absolute operation/trip-state path. The MCP process may
   create/read it, but ACLs and process isolation must deny the execution agent
   direct file access. Verify read-only diagnostics show `unlatched`.
3. Record the operator-approved compliance voltage without committing it.
4. Put the supply in parallel tracking mode through an operator-controlled
   procedure; the MCP intentionally exposes no tracking-mode command.
5. Keep all other clients read-only during the MCP demonstration; gateway batch
   isolation is not a workflow-duration exclusive-writer lease.
6. Start with power control disabled, read state, and verify model, serial,
   parallel mode, both outputs, native setpoints, and physical wiring.
7. Obtain fresh human confirmation of the applicable physical state
   immediately before enabling output.
8. Validate prepare, zero-current enable, one 0.2 A load-current step, pressure
   observation, decrease, and shutdown in that order.

For the staged unloaded-HIL acceptance test, use a 1.0 V fixed compliance
voltage and lower the operator load-current ceiling to 0.2 A. Set parallel mode
out-of-band with outputs off, perform the initial read with control disabled,
then restart with the same `unloaded_hil` policy and control enabled. The human
must freshly verify that no dispenser or unapproved load is connected and that
any present metrology wiring is operator-approved immediately before the
unloaded-HIL connection confirmation is submitted. Production dispenser
confirmation may not be reused.

Any finite post-operation native measured-current sample with
`abs(I) > 0.001 A`, or any unavailable/non-finite measurement, now ends the run,
returns an explicit trip error, and blocks later MCP mutations. The operator must
physically verify or shut down the supply if recovery is uncertain. Reset is a
privileged out-of-band human action; no MCP reset exists, and physical
emergency-button/reset-service integration remains pending. A supervised
fresh-sample characterization procedure remains a future physical acceptance
item.

Before unattended conditioning, add and validate a persistent output lease,
pressure freshness and trip logic, auditable run state, a physical
interlock/watchdog, and the separate activation-decision policy. Software
shutdown is not a physical emergency stop.
