# Raspberry Pi Research Quick Commissioning

This is the supervised source-checkout workflow. Use Git, uv, and Python 3.13
on the Pi. First commissioning is control-disabled and read-only.

For a hardware-free second computer, use the [internal simulation backend](../../README.md#independent-simulation-host)
instead of this real-instrument commissioning procedure. No Pi change is required.

## Install and configure

```sh
git clone --recurse-submodules https://github.com/SinclairQuantumLab/dispenser-mcp-server.git
cd dispenser-mcp-server
uv sync
# Fresh checkout only: do not overwrite existing operator files.
cp settings/mcp-settings.toml.template settings/mcp-settings.toml
cp settings/hicube-neo-client-settings.toml.template settings/hicube-neo-client-settings.toml
cp settings/py-siglent-spd3000/gateway-settings.toml.template settings/py-siglent-spd3000/gateway-settings.toml
cp settings/py-siglent-spd3000/gateway-auth.toml.template \
  settings/py-siglent-spd3000/gateway-auth.toml
chmod 600 settings/py-siglent-spd3000/gateway-auth.toml
```

Templates are tracked; actual TOMLs are ignored recursively by settings/.gitignore.
For an existing clone, back up settings outside the checkout **before pulling**
the one-time template migration: Git can remove clean tracked TOMLs or block on
local edits. Preserve/restore your copies; never discard them with reset/checkout.

Fill the placeholders in the three nonsecret settings files:
`settings/mcp-settings.toml`, `settings/hicube-neo-client-settings.toml`, and
`settings/py-siglent-spd3000/gateway-settings.toml`. Put the gateway token only
in the untracked `settings/py-siglent-spd3000/gateway-auth.toml`.

Main listener settings are top-level:

```toml
control_enabled = false
allow_remote_access = true
port = 8000
```

False is the default for both booleans. Remote access true binds all IPv4
interfaces; false binds only 127.0.0.1. Hardware control is a separate setting.
Use the Pi's actual IP or resolvable hostname, such as
`http://<Pi-IP>:8000/mcp` or `http://raspberrypi.local:8000/mcp`.
No SSH tunnel or manual Host list is required. The native MCP client must omit
the Origin header on `/mcp`. Open `/dashboard` in a browser for passive records. This pilot
does not authenticate incoming MCP clients.

## Start and check

```sh
uv run python -m dispenser_conditioning_mcp.deployment_check
uv run dispenser-conditioning-mcp
```

Keep the process running and register its URL in your MCP host. Startup and the
offline check do not contact hardware. Discover the seven tools, then call only
`read_vacuum_pressure` and `read_dispenser_power_state`. Confirm pressure
provenance, exact PSU identity, parallel mode, outputs off, and disabled control.

Operator diagnostics may include nonsecret paths, endpoints, and failure context.
The offline checker provides stage codes; `--diagnostic` adds exception class.
Model-facing tool errors remain sanitized. Never share authentication contents.

Normal prepare/enable/set now also require brief `action_context` referencing
server-issued session/observation IDs. Shutdown still accepts `{}`; an explicit
`record_conditioning_decision` can declare completion without actuation. See the
[unreleased input extension](../../docs/session-recording-contract.md).

For a later supervised power run, preserve the fixed 2.4 A native/4.8 A commanded
load ceilings, exact 0.2 A load upward steps, identity checks, and fresh physical
wiring confirmation. No-load test keeps post-action current checks and a process-local stop latch.
There is no state file or software between-session inspection gate; the nearby
operator handles physical checks. Explicit shutdown remains available after a trip. See the
[power contract](../../docs/power-control-contract.md) and
[acceptance sequence](../../README.md#minimal-no-load-test-acceptance-sequence).

## Updating an existing checkout

Back up your edited nonsecret settings before pulling: tracked TOMLs can conflict
with upstream edits. Keep acceptance context, serial, compliance, control, device
settings, and the untracked authentication file. Remove `transport` and the
entire `[streamable_http]` table. Add `allow_remote_access` and `port` at the
top level; old transport options are rejected. Then run `uv sync`.

Historical hardened deployment scripts and reports are reference-only. Their
offline bundles, manifest/ACL audits, and SSH setup are not prerequisites for
this pilot. No wheel build or broad test campaign is required.
