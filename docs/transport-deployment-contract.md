# Transport and Deployment Contract

Package version 0.5.1 retains the startup-only transport boundary and adds a
manifest-bound, index-disabled dedicated-host Python installation, complete
base-runtime tree validation, and a recursive exact ACL/owner boundary. The
Python-payload manifest covers only the lock, inventory, MCP wheel, and exact
dependency wheelhouse; other deployment artifacts keep separate approved hashes.
This does not change the six-tool conditioning contract or the v0.4.3
power-safety semantics.

## Startup settings

| Variable | Default | Contract |
| --- | --- | --- |
| `DISPENSER_MCP_TRANSPORT` | `stdio` | Exact `stdio` or `streamable-http`; all other values, including deprecated SSE, are rejected |
| `DISPENSER_MCP_HTTP_BIND_HOST` | `127.0.0.1` | HTTP only; must resolve syntactically to an explicit loopback literal/name |
| `DISPENSER_MCP_HTTP_PORT` | `8000` | HTTP only; integer 1024–65535 |
| `DISPENSER_MCP_HTTP_PATH` | `/mcp` | HTTP only; one absolute, non-root path without query, fragment, whitespace, backslash, or trailing slash |
| `DISPENSER_MCP_HTTP_TRUST_MODE` | `loopback_only` | Exact `loopback_only`, `authenticated_ssh_tunnel`, or `authenticated_reverse_proxy` |
| `DISPENSER_MCP_HTTP_ALLOWED_HOSTS` | none | Reverse-proxy mode only; comma-separated exact Host header values with no wildcard |
| `DISPENSER_MCP_HTTP_ALLOWED_ORIGINS` | none | Reverse-proxy mode only; comma-separated exact HTTPS origins with no wildcard |

HTTP startup also requires the existing
`DISPENSER_SIGLENT_CONTROL_ENABLED=true|false` policy explicitly. Missing or
invalid values reject startup before the configured MCP application imports.

Stdio remains fully backward-compatible when all HTTP-only variables are
absent. Supplying an HTTP-only variable while stdio is selected is rejected so
stale deployment settings cannot be silently ignored.

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
processes, profiles, auth paths, ports, and acceptance contexts. The HIL process
alone has its own unique durable state path. Only one process may target one PSU
at a time. Process lifecycle, environment, credentials, authentication files,
durable state, reset, logs, and network bridge remain operator-owned.

The MCP client receives only an authenticated endpoint and the production tool
surface. No tool accepts transport, bind, port, path, credentials, safety
policy, durable state, or reset parameters.

See the [dedicated Windows runbook](../deployment/windows/README.md) for the
commissioning and rollback procedure.
