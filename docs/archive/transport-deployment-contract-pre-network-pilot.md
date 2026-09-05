# Historical reference — superseded for the research pilot

This record is retained for history, not an active deployment or testing gate.

# Transport and Deployment Contract

Package version 0.6.1 retains the strict repository-local TOML documents
introduced in 0.6.0 and adds stage-coded, sanitized offline startup diagnostics.
This does not change the six-tool
conditioning contract or the v0.4.3 power-safety semantics.

## Startup settings

All values below are keys in `settings/mcp-settings.toml`. Unknown keys or wrong
TOML types reject startup.

| Key | Default | Contract |
| --- | --- | --- |
| `transport` | `stdio` | Exact `stdio` or `streamable-http`; all other values are rejected |
| `control_enabled` | `false` | Strict TOML boolean; loopback-only HTTP cannot enable control |
| `streamable_http.bind_host` | `127.0.0.1` | HTTP only; must be an explicit loopback literal/name |
| `streamable_http.port` | `8000` | HTTP only; integer 1024–65535 |
| `streamable_http.path` | `/mcp` | HTTP only; one absolute, non-root path without query, fragment, whitespace, backslash, or trailing slash |
| `streamable_http.trust_mode` | `loopback_only` | Exact `loopback_only`, `authenticated_ssh_tunnel`, or `authenticated_reverse_proxy` |
| `streamable_http.allowed_hosts` | `[]` | Reverse-proxy mode only; exact Host strings with no wildcard |
| `streamable_http.allowed_origins` | `[]` | Reverse-proxy mode only; exact HTTPS origin strings with no wildcard |

The `streamable_http` table is rejected while stdio is selected, preventing
stale HTTP settings from being silently ignored. HTTP settings cannot be passed
as MCP arguments, CLI flags, or environment variables.

## Fixed HTTP behavior

- Bind is always loopback; `0.0.0.0`, `::`, a LAN address, and a DNS name other
  than `localhost` are rejected.
- DNS-rebinding protection is always enabled.
- Host and Origin checks are exact; this component generates no wildcard.
- Request body size is fixed at 256 KiB.
- JSON response mode and stateless mode remain disabled for SDK-compatible
  stateful Streamable HTTP transport operation. This protocol session is not an
  experimental run-state or decision orchestrator.
- Control-enabled HTTP rejects `loopback_only`.
- `authenticated_ssh_tunnel` permits only the selected loopback Host headers
  and rejects custom Host/Origin settings.
- `authenticated_reverse_proxy` requires at least one exact proxy-facing Host;
  loopback Host values are also accepted for the operator-owned backend hop.
- The server does not expose a bearer-token implementation or unauthenticated
  health route.

Trust mode is an operator assertion, not authentication. The named bridge must
actually exist outside this process and must be inaccessible to the AI agent.
An SSH tunnel must use a protected key and constrained forwarder identity. A
reverse proxy must enforce reviewed OAuth 2.1 or mTLS and authorize a single
writer before forwarding.

## Process boundary

Test-unit unloaded HIL and production dispenser deployments use separate
processes, source checkouts/settings, auth paths, ports, and acceptance contexts. The HIL process
alone has its own unique durable state path. Only one process may target one PSU
at a time. Process lifecycle, settings, credentials, authentication files,
durable state, reset, logs, and network bridge remain operator-owned.

The MCP client receives only an authenticated endpoint and the production tool
surface. No tool accepts transport, bind, port, path, credentials, safety
policy, durable state, or reset parameters.

See the [dedicated Windows runbook](../deployment/windows/README.md) for the
commissioning and rollback procedure.
