# Raspberry Pi Research Quick Commissioning

This is the current source-checkout path for package 0.6.0. It uses the same
strict TOML settings as every supported development host. It is not the future
hardened/offline systemd release bundle.

First commissioning is control-disabled and read-only. No install or startup
command below contacts a device; the two read tools do so only when called.

## Prerequisites

- Raspberry Pi OS 64-bit on `aarch64`;
- Git and `uv`;
- Python 3.13; and
- operator-approved endpoint, identity, compliance, acceptance-context, and
  gateway-token values.

```sh
test "$(uname -m)" = aarch64
git --version
uv --version
```

## Clone, configure, and validate

```sh
git clone --recurse-submodules https://github.com/SinclairQuantumLab/dispenser-mcp-server.git
cd dispenser-mcp-server
git submodule status
uv sync
cp settings/py-siglent-spd3000/gateway-auth.toml.template \
  settings/py-siglent-spd3000/gateway-auth.toml
chmod 600 settings/py-siglent-spd3000/gateway-auth.toml
```

The submodule line must begin with a space, not `-` or `+`. Edit these files:

```text
settings/mcp-settings.toml
settings/hicube-neo-client-settings.toml
settings/py-siglent-spd3000/gateway-settings.toml
settings/py-siglent-spd3000/gateway-auth.toml
```

Fill every placeholder. Put only the token in `gateway-auth.toml`, never commit
that file, and keep `control_enabled = false`. The MCP derives the vendored
HiCube file, Siglent submodule source, settings directory, and authentication
path from the checkout. Do not configure them as environment variables.

```sh
uv run python -m dispenser_conditioning_mcp.deployment_check
uv run dispenser-conditioning-mcp
```

Stdio is the default. For first commissioning, discover the six tools and call
only `read_vacuum_pressure` and `read_dispenser_power_state`. Confirm pressure
provenance, exact PSU identity, parallel mode, outputs off, and
`control_enabled=false`, then stop.

Streamable HTTP remains an optional loopback-only transport configured in
`mcp-settings.toml`. Authentication or SSH forwarding remains operator-owned;
see the [transport contract](../../docs/transport-deployment-contract.md).

## Boundary

This quick path does not provide a hardened service identity, boot service,
offline wheelhouse, authenticated release bundle, SSH policy, or physical
watchdog. Do not enable output or change current during first commissioning.
Any later live mutation still requires the existing protected durable-state
boundary, physical verification, fresh human confirmation, and safety review.
