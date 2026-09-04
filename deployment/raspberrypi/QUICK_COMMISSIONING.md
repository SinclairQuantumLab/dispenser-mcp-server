# Raspberry Pi Research Workflow

This is the current research-and-development path. It runs the MCP from a Git
checkout with a pinned Siglent Git submodule and `uv`. It does not use a wheel,
GitHub Release asset, offline wheelhouse, or release manifest. Those mechanisms
belong to the future hardened deployment note.

The public six-tool contract and all deterministic hardware safety checks remain
unchanged. Start with power control disabled and perform only read-only
commissioning first.

## Host prerequisites

- Raspberry Pi OS 64-bit with `git` and `uv` available.
- CPython 3.13, matching `.python-version` and `requires-python`.
- An operator-owned checkout and startup configuration.
- Device endpoint, expected identity, gateway authentication file, and HIL state
  path kept outside agent-visible source control.

Confirm the host:

```sh
test "$(uname -m)" = aarch64
python3.13 -c 'import platform,sys; assert sys.version_info[:2] == (3,13); assert platform.machine() == "aarch64"'
git --version
uv --version
```

## Update and synchronize

From the `mcp-server` checkout:

```sh
git pull --ff-only
git submodule update --init --recursive
git submodule status
uv sync --all-groups
```

The Siglent line must begin with a space, not `-` or `+`, and currently resolves
to parent-pinned commit
`0984bba67d8e5651cd2d9aa7b2c0db2d6eb694f3`. `uv sync` installs
`py-siglent-spd3000` from `dependencies/py-siglent-spd3000` as an editable local
path source and installs all dependencies declared by that project. Do not add
an independent Siglent wheel or VCS URL to this workflow.

The same parent checkout already contains the canonical HiCube client at
`dependencies/hicube/hicube_neo_client.py`; do not clone or install a separate
HiCube relay repository. Its provenance note records the reviewed upstream
commit and SHA-256.

Run the offline development gates:

```sh
uv run ruff check .
uv run pyright
uv run pytest -q
```

## Configure and run control-disabled

Copy `.env.example` to an operator-owned location outside the checkout and fill
the placeholders without committing it. Set the driver source to the absolute
submodule source path:

```sh
export DISPENSER_SIGLENT_DRIVER_SRC="$(pwd)/dependencies/py-siglent-spd3000/src"
export DISPENSER_HICUBE_CLIENT_FILE="$(pwd)/dependencies/hicube/hicube_neo_client.py"
export DISPENSER_SIGLENT_CONTROL_ENABLED=false
```

Create `settings/py-siglent-spd3000-gateway-auth.toml` from its tracked
`.template` using an operator-only editor. The live file is ignored and is the
development default when `DISPENSER_SIGLENT_GATEWAY_AUTH_FILE` is omitted.
Deployed layouts may instead set that variable to another protected absolute
path. Do not print or copy the authentication value into shell history, source
control, or agent context.

For local development, stdio is the simplest transport:

```sh
uv run dispenser-conditioning-mcp
```

For a separately running Pi process, use the existing loopback-only Streamable
HTTP settings and an operator-owned tunnel from the transport contract. Keep the
process control-disabled during first commissioning. Discover exactly six tools,
then call only `read_vacuum_pressure` and `read_dispenser_power_state`. Confirm
pressure provenance, exact PSU identity, live parallel mode, outputs off, and
`control_enabled=false` before stopping.

Do not enable output or change current in this first run. Missing, malformed,
pending, or tripped HIL state remains fail-closed. Any later live mutation still
requires the existing physical verification, operator-owned durable-state
initialization/reset boundary, fresh human confirmation, and safety review.
