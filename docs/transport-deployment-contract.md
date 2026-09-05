# Transport and deployment contract

The supervised research server always uses Streamable HTTP at the fixed
`/mcp` path. It starts from its source checkout using `uv sync` and
`uv run dispenser-conditioning-mcp`; the pinned Siglent submodule stays editable.

## Main settings

```toml
allow_remote_access = false
port = 8000
```

The boolean must be a TOML boolean, not a string or integer. False binds
`127.0.0.1`; true binds `0.0.0.0`. The port is an integer from 1024 through
65535. Connect with `http://127.0.0.1:8000/mcp` locally or
`http://<server-IP>:8000/mcp` remotely. Resolvable hostnames work remotely too.
Use the selected port in the URL. No interface discovery or LAN/Internet
classification occurs.

There is no public transport selector, bind_host, trust_mode, allowed_hosts,
allowed_origins, path, or streamable_http table. These old keys fail validation.
Network exposure does not change the independent control_enabled setting.

## Native-client request policy

MCP requests to /mcp with an Origin header, including empty or null values, receive
403. Native MCP clients omit this header. Browser client support requires a
later explicit policy. The local listener also uses SDK exact Host checks for
127.0.0.1 and localhost at the configured port. The remote listener accepts
ordinary IP and hostname Host values without an operator allowlist.

The SDK retains Content-Type validation and a 256 KiB request-body limit.
This policy is not authentication; incoming MCP clients are not authenticated
by this pilot. The operator decides where the listener is reachable. SSH
forwarding may be used but is not required for control-enabled startup.

## Workflow boundaries

Startup validates local configuration without connecting to instruments.
The six hardware tools plus one non-actuating declaration tool are served.
The identity checks, physical enable confirmations, fixed
current ceilings and steps, and unloaded-HIL durable interlock/reset boundaries
are unchanged. Keep one writer per physical PSU. The operator owns live process
administration, credentials, and initialization/reset; the execution agent uses
MCP tools during the run.

Operator diagnostics may show useful nonsecret settings, paths, endpoints, and
failure context. Model-facing errors remain sanitized, and tokens belong in
neither output. Source development may use ordinary local debugging.

The existing HTTP app also serves the passive `/dashboard` and its data/assets.
Recording occurs only on tool calls, not dashboard polls. See the
[session recording contract](session-recording-contract.md).

See the [Pi quick guide](../deployment/raspberrypi/QUICK_COMMISSIONING.md).
The [previous transport contract](archive/transport-deployment-contract-pre-network-pilot.md)
is historical. Hardened offline bundles and broad release audits do not gate
the current supervised research workflow.
