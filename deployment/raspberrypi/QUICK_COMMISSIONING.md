# Raspberry Pi Research Quick Commissioning

This is the supervised source-checkout workflow. Use Git, uv, and Python 3.13
on the Pi. First commissioning is control-disabled and read-only.

## Install and configure

```sh
git clone --recurse-submodules https://github.com/SinclairQuantumLab/dispenser-mcp-server.git
cd dispenser-mcp-server
uv sync
cp settings/py-siglent-spd3000/gateway-auth.toml.template \
  settings/py-siglent-spd3000/gateway-auth.toml
chmod 600 settings/py-siglent-spd3000/gateway-auth.toml
```

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
the Origin header; browser clients need a future explicit policy. This pilot
does not authenticate incoming MCP clients.

## Start and check

```sh
uv run python -m dispenser_conditioning_mcp.deployment_check
uv run dispenser-conditioning-mcp
```

Keep the process running and register its URL in your MCP host. Startup and the
offline check do not contact hardware. Discover the six tools, then call only
`read_vacuum_pressure` and `read_dispenser_power_state`. Confirm pressure
provenance, exact PSU identity, parallel mode, outputs off, and disabled control.

Operator diagnostics may include nonsecret paths, endpoints, and failure context.
The offline checker provides stage codes; `--diagnostic` adds exception class.
Model-facing tool errors remain sanitized. Never share authentication contents.

For a later supervised power run, preserve the fixed 2.4 A native/4.8 A commanded
load ceilings, exact 0.2 A load upward steps, identity checks, and fresh physical
wiring confirmation. Unloaded-HIL additionally requires its existing durable
state initialized outside MCP after physical verification. Its trip, pending
guard, and operator-only reset boundaries remain in force. See the
[power contract](../../docs/power-control-contract.md) and
[acceptance sequence](../../README.md#minimal-unloaded-hil-acceptance-sequence).

## Updating an existing checkout

Back up your edited nonsecret settings before pulling: tracked TOMLs can conflict
with upstream edits. Keep acceptance context, serial, compliance, control, device
settings, and the untracked authentication file. Remove `transport` and the
entire `[streamable_http]` table. Add `allow_remote_access` and `port` at the
top level; old transport options are rejected. Then run `uv sync`.

Historical hardened deployment scripts and reports are reference-only. Their
offline bundles, manifest/ACL audits, and SSH setup are not prerequisites for
this pilot. No wheel build or broad test campaign is required.
