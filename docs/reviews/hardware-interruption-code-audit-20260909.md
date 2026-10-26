# Hardware interruption: deterministic code audit

Date: 2026-09-09. Read-only audit of current checkout, installed SDK, actual
caller helpers and public logs. No endpoint calls, hardware actions, tests,
restart or runtime edits. This report is not a scientific outcome assessment.
Source lines refer to the inspected checkout; scratch helpers may subsequently change.

## Findings and priority

### High: overlapping one-shot callers reuse request IDs in one MCP transport

`../.codex-tmp/decision-remote-sixth-20260908/client.py:33–40,43–46,145–154`
initializes each PublicClient sequence to zero, then the convenience function
loads a saved MCP session without restoring a session-wide sequence. Different
processes therefore issue ID 1 on the same session. Sequential reuse alone does
not establish a collision; overlapping outstanding requests do.

The installed SDK `mcp/server/streamable_http.py:601–607,641–645` indexes request
streams by request ID inside the session. The caller matches only that ID
(`client.py:74`), not the requested semantic tool or a unique caller nonce.
At 19:57:38 UTC an ID-1 pressure request received an ID-1 PSU response whose
server receipt preceded the pressure request. Duplicate-ID misrouting is strongly
supported, although it does not explain the original pending PSU read by itself.
Output-schema validation rejected the wrong pressure source; no evidence it was
accepted as a pressure measurement.

Minimum fix: one transport owner with serialized outstanding operations, or
distinct initialized transports per concurrent caller; globally unique request IDs
within each shared session. Avoid hand-maintaining JSON-RPC where the official SDK
already owns correlation. Do not start extra same-session recovery queries while
an earlier operation remains unresolved.

### High: transport, tool, local validation and bookkeeping errors collapse

`client.py:54–90` records network phase and rethrows, without an automatic retry
or OFF. The 45-second socket timeout is not a proven end-to-end cancellation or
proof that the server did not execute. `response.read()` consumes the entire
body before JSON/SSE parsing; a body timeout cannot identify an instrument fault.
`client.py:111–136` performs input validation, invocation, telemetry acknowledgement,
tool-error handling and output validation, but exposes ordinary exceptions for
all these stages. Output validation may fail **after an action succeeded**.
The JSONL append in `rpc`'s `finally` can itself mask a successful result or the
original exception. Telemetry-error logging can also throw while handling an
otherwise optional telemetry failure (`client.py:28–30,117–120,129–134`).

Minimum fix: retain the original result and stage in a small caller error/result
envelope; distinguish transport unknown, returned tool error, local schema error,
and bookkeeping failure. A read failure is loss of observation, not evidence of
changed output. A post-write unknown must not invite blind retry. No automatic
OFF follows from this helper: the decision/operator policy selects it separately.

### Medium: acquisition age and result delivery age are not equivalent

`domain.py:78–104` validates timezone, positive finite pressure and nonempty serial,
not freshness. The bundled HiCube client stamps collector time after its read
(`dependencies/hicube/hicube_neo_client.py:387–409`); it requests OPC UA source
timestamps but does not expose them. `client.py` has no generic freshness or
session/source checks beyond optional catalog output validation. Historical
`read_batch.py:25–49` checks several identities/values but not elapsed sample age;
it writes latest-power before all its assertions. Other one-off commands add
explicit age checks, so guarantees differ by entrypoint. Cached latest files
persist after failed reads and must remain labelled with their original times.

Minimum fix: a shared read-result check that retains observation, receipt and
local delivery timestamps and validates expected session/source; report stale or
unverified readings without converting them into an equipment anomaly. Keep
decision authority separate rather than adding another automatic cutoff.

### Medium: operator diagnostics and execution labels lose useful precision

`siglent.py:219–237,517–524` chains detailed read/open exceptions behind generic
PowerControlError messages. `server.py:450–455` turns the public message into
ToolError; expected errors are not necessarily printed with the original cause.
This explains why a useful underlying exception may be absent from ordinary
operator logs. `recording_service.py:274–280` calls any error after handler entry
`failed_or_unknown`; that is a dispatch label, not proof of a write. A pre-write
identity/limit failure may therefore share this broad label with a post-write
failure. Public error text remains essential.

Minimum fix: credential-safe operator exception chaining and a small explicit
write-effect classification carried through existing result metadata. Do not
replace useful causes with undifferentiated “unavailable” or claim all failures
mean uncertain physical output.

## Actual code-to-physical-effect boundaries

| Condition | Current path | Physical effect |
|---|---|---|
| Pressure read fails | `hicube.py:71–123`, `server.py:211–221` | Close pressure connection; return error; no PSU command. |
| PSU diagnostic read fails | `siglent.py:219–237` | Close session; error; no recovery OFF. |
| Invalid context/reference or pre-dispatch logging failure | `recording_service.py:177–244` | Normal action not dispatched. Shutdown does not depend on this logging. |
| Identity/control authorization/latch/target/topology/compliance/CAS rejection before first write | `siglent.py:241–378,414–477` | Reject; no recovery OFF merely because validation failed. |
| Exception after first attempted mutation write | `siglent.py:433–510` | Best-effort two-channel OFF, verify, zero setpoints; report verified recovery or uncertainty. This is intentional deterministic recovery, including production mode. |
| No-load post-action current outside ±1 mA, nonfinite or unavailable | `siglent.py:633–720` | Process-local latch and verified OFF attempt; applies only to `no_load_test`, not normal production heating. |
| Explicit shutdown request | `siglent.py:380–412,414–427` | OFF then zero/verify; bypasses no-load latch but retains identity and operator control permission. |
| Client timeout/schema error, pressure review notification, or completion declaration | Caller / recording layer | No inherent PSU write. Separate agent-selected shutdown can follow. |

The power RLock serializes read/control operations (`siglent.py:212,223,419`);
HiCube has its own read lock (`hicube.py:69–74`). A stalled power operation may
delay later power operations, including OFF. The installed SDK **offloads sync
tool callbacks to worker threads**, `mcp/server/mcpserver/utilities/func_metadata.py:164`.
Therefore the earlier hypothesis that ordinary `def` handlers directly block the
event loop is unsupported. Worker exhaustion, SDK/session routing, other blocking
work and network/process conditions remain possibilities, not diagnosed causes.
Simulator faults and simulator clock logic are not causes of these hardware events.

## Correlated interruption evidence (UTC, September 9)

Public logs: `../.codex-tmp/hardware-first-readiness-20260908/dual-channel-restart/public-client.jsonl`
and its `recovery-0352/public-client.jsonl`. Detailed later chronology:
`recovery-0352/interruption-20260909T2002Z.md`.

| Event | Observed failure | Explicit subsequent action and independent result |
|---|---|---|
| 02:49 | 02:48:35 pressure request: RemoteDisconnected at response headers in 0.0007 s; preceding PSU read ON 1.2 A at 02:48:24. | Shutdown requested 02:48:53; returned OFF/zero 02:49:07; independent OFF/zero 02:49:09. Reused idle connection is plausible, not proven cause. |
| 03:19 | 03:18:06 PSU request: response-body timeout after 45.05 s; pressure had just succeeded. | Shutdown requested 03:19:13; OFF/zero 03:19:23; independent OFF/zero 03:19:25. Timeout did not establish equipment failure. |
| 20:02 | Pending PSU calls and the wrong ID-1 envelope above. A new-transport PSU read observed ON 2.6 A at 20:01:45, but its result was collected after shutdown initiation according to the interruption note. | Separate-transport shutdown received 20:02:11; OFF/zero 20:02:24; independent OFF/zero 20:04:03 and 20:04:27. This was discretionary monitoring-recovery policy, not a no-load trip. |

The 19:35–19:50 observation gap coincided with caller compaction according to the
public interruption note; no HTTP failure establishes its scheduling cause.
Earlier LAN TCP acceptance with no dashboard HTTP response supports an MCP-side
request-processing stall at that time, not proof of device disconnection.

## Coverage and limits

Inspected current server registration, adapter/controller, recording wrappers,
pressure normalization, installed SDK dispatch/routing, actual shared caller,
read-batch helper, and the three public-log windows. No current operational
settings/credentials, private simulator truth or unrelated conversations read.
No pending request was cancelled and no active caller modified. Current files are
not guaranteed byte-identical to every historical invocation; ad-hoc shell calls
can override sequence/session handling. Full Pi logs, packet traces and server
pending-task state were unavailable, so initial hang causes remain unproven.
Prioritize correlation and error-classification fixes before adding new services,
retry engines, watchdogs or blanket OFF policies. This audit authorizes none of them.
