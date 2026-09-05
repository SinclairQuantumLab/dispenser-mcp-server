> Historical reference — retained for provenance, not an active deployment or
> testing gate. Current source-checkout research instructions are in the
> server README and deployment/raspberrypi/QUICK_COMMISSIONING.md. Legacy
> settings, hardened bundles, and commands below may not match the current pilot.

# Dedicated Windows Host Deployment

> **Status:** Deferred pre-0.6 deployment reference. The launcher and PSD1
> profiles below target the archived 0.5.1 environment interface and do not
> configure package 0.6.1. Use the source-checkout TOML Quick Start in the root
> README until these service wrappers receive a reviewed TOML migration.

This runbook deploys the Dispenser Conditioning MCP as an independently
managed process under a non-agent Windows identity. The AI client receives only
an MCP endpoint and the six documented tools. It does not receive process
control, environment variables, device endpoints, gateway authentication,
configuration files, durable HIL state, or reset authority.

The recommended fast path is:

1. run native Streamable HTTP on `127.0.0.1` under the dedicated account;
2. keep power control disabled for installation and read-only commissioning;
3. have an operator-managed SSH tunnel expose that loopback endpoint to the MCP
   client; and
4. move to an operator-owned authenticated reverse proxy later if a stable
   OAuth 2.1 or mTLS service boundary is required.

The SSH tunnel carries HTTP. It is not the stdio SSH fallback: the MCP server
remains an independent process when the tunnel reconnects. The component also
retains stdio as its default development transport.

## Security boundary

The installed MCP SDK supports Streamable HTTP directly, including fixed
Host/Origin checks and request-body limits. It does not turn an arbitrary
pre-shared token into standards-compliant MCP authorization. The SDK's native
authorization boundary requires an operator-provided OAuth 2.1 authorization
server and a real `TokenVerifier`. This component deliberately does not invent
a bearer-token verifier.

Version 0.5.1 therefore enforces all of these startup rules:

- omitted `DISPENSER_MCP_TRANSPORT` means `stdio`;
- only `stdio` and `streamable-http` are accepted;
- Streamable HTTP binds only to `127.0.0.1`, `::1`, or `localhost`;
- HTTP requires an explicit `DISPENSER_SIGLENT_CONTROL_ENABLED` value;
- `loopback_only` refuses control-enabled startup;
- control-enabled HTTP requires the operator assertion
  `authenticated_ssh_tunnel` or `authenticated_reverse_proxy`;
- reverse-proxy mode requires exact allowed Host values and allows only exact
  HTTPS Origin values; and
- no MCP tool can change transport, bind, port, path, trust mode, credentials,
  device identity, acceptance context, state path, or safety policy.

The trust-mode value is an assertion about an external operator-owned boundary;
it is not authentication by itself. Selecting it without actually deploying
the named authenticated bridge violates this deployment contract.

Primary references:

- [MCP Python SDK transports](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md)
- [MCP Python SDK authorization](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/authorization.md)
- [MCP Streamable HTTP transport requirements](https://modelcontextprotocol.io/specification/draft/basic/transports)
- [MCP authorization requirements](https://modelcontextprotocol.io/specification/draft/basic/authorization)
- [Microsoft OpenSSH Server installation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Microsoft OpenSSH configuration](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)

## Required operator inputs

Before commissioning, record outside the repository:

- supported Windows edition/version and patch status;
- dedicated local or domain service-account name;
- independently approved SHA-256 values for the Python-payload manifest,
  payload validator, runtime manifest, runtime validator, `uv`, MCP wheel,
  HiCube client, Siglent source archive, launcher, initializer, installer, ACL
  validator, and profile templates;
- independently approved CPython installer SHA-256 and Authenticode signer
  thumbprint;
- absolute installed HiCube client and Siglent driver paths;
- device/gateway resource values and exact PSU model/serial;
- protected gateway-auth file path;
- separate test and production compliance/current policies;
- whether the remote boundary is an SSH tunnel or authenticated reverse proxy;
- allowed client source address for the Windows firewall; and
- operator owners for process restart, physical shutdown, and HIL reset.

Do not place any of these values in Codex configuration, chat, a committed
profile, a command transcript, or an agent-readable directory.

Before executing any transferred PowerShell file, compare its SHA-256 with its
separately authenticated, exact site-approved hash. This includes the
protected-root initializer, Python installer, ACL validator, launcher, runtime
manifest tools, and any site-owned wrapper. The Python-payload manifest does
not cover these scripts, `uv`, the base runtime, profiles, external instrument
clients, or other transferred root files. Each requires its own approval/hash
input or the explicit review step documented below. The supplied scripts are
not Authenticode-signed.

## Host prerequisites

- A site-approved offline CPython 3.13 x64 installer with an independently
  obtained SHA-256 and Authenticode signer-certificate thumbprint.
- A procedurally clean CPython installation whose complete file tree and
  reviewed installer identity are co-recorded by an approved runtime manifest;
  hashing `python.exe` alone is not sufficient integrity coverage.
- A reviewed `uv` executable with an independently obtained SHA-256.
- A reviewed Windows CPython 3.13 x64 Python-payload bundle with a complete
  wheelhouse, closed Python-payload manifest, and independently obtained
  manifest SHA-256. The deployment host needs no package index.
- One dedicated, enabled local MCP service user that is not a direct member of
  local Administrators.
- One separate enabled local deployment-operator user. It is the exact owner of
  protected deployment objects outside service-writable state and logs.
- One separate operator/bridge identity if SSH tunneling is used.
- NTFS protected application, configuration, authentication, state, and log
  directories.
- Windows OpenSSH Server for the SSH-tunnel path, or an operator-owned reverse
  proxy that enforces mTLS or OAuth 2.1 before forwarding to loopback.
- Firewall rules that expose only the bridge listener. The MCP HTTP port itself
  remains loopback-only and needs no inbound LAN rule.
- A single authorized writer. All other clients must remain read-only while a
  conditioning sequence is active.

The MCP is not a workflow orchestrator, decision service, output lease,
watchdog, or physical E-stop. The agent sequences calls; the MCP enforces
strict per-call contracts and deterministic negative safety authority.

## Prepare the reviewed offline Python bundle

`uv.lock` is the development lock and is not, by itself, a deployment
mechanism. The deployment bundle must include all of the following:

- `python-dependencies.lock.txt`, whose Windows CPython 3.13 AMD64 target set is
  marker-free and whose requirements have exact versions and artifact hashes;
- `python-runtime-inventory.json`, which records the exact installed
  distributions expected on Windows CPython 3.13 x64;
- one reviewed wheel for every selected dependency in a `wheelhouse`
  directory;
- the separately built MCP wheel; and
- `release-manifest.json`, created by the reviewed
  `release_bundle_manifest.py` tool.

The deployment scripts, schemas, launcher, and profile templates are reviewed
release tooling outside that Python bundle. Authenticate each tool hash through
the site's software-release channel before running it.

On a controlled release workstation that matches Windows x64 and CPython 3.13,
populate an empty wheelhouse from the committed lock. This is the only step
that may use a package index, and the release operator must archive its command
log and approve every downloaded hash before transfer:

```powershell
py -3.13 -m pip download `
    --require-hashes `
    --only-binary=:all: `
    --dest C:\ReleaseStaging\release-bundle\wheelhouse `
    --requirement .\python-dependencies.lock.txt

py -3.13 -I -B .\release_bundle_manifest.py create `
    --bundle-root C:\ReleaseStaging\release-bundle `
    --dependency-lock-file python-dependencies.lock.txt `
    --runtime-inventory-file python-runtime-inventory.json `
    --mcp-wheel-file dispenser_conditioning_mcp-0.5.1-py3-none-any.whl `
    --output C:\ReleaseStaging\release-bundle\release-manifest.json
```

Place the lock, inventory, MCP wheel, and `wheelhouse` under the shown bundle
root before creating the manifest. The creator rejects a missing/extra wheel, a
non-target lock marker or member, version drift, a wheel hash absent from the
lock, an inventory mismatch, and reparse-point artifacts. It also opens each
dependency and MCP wheel, rejects unsafe/duplicate archive members and
non-Windows native libraries, requires one matching dist-info identity, and
requires internal WHEEL tags to exactly match the compatible filename tags.
Record the SHA-256 of `release-manifest.json` and the release-validator script
through a channel independent of the bundle. The host installer consumes both
approved values and re-verifies the exact filename/hash set plus lock/inventory
correspondence.

Do not run the download command on the dedicated host. A bundle without every
manifest-declared wheel is not deployable, and preflight rejects it before the
virtual environment is resolved or changed.

## Create the dedicated account and protected root

Run the following in an elevated operator PowerShell. Replace account names
only in the operator session; do not save the entered password in a script.

```powershell
$McpServiceAccount = "svc-dispenser-mcp"
$McpRoot = "C:\ProgramData\DispenserConditioningMcp"
$DeploymentOperator = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$McpPassword = Read-Host "Dedicated MCP service-account password" -AsSecureString
New-LocalUser -Name $McpServiceAccount -Password $McpPassword `
    -Description "Dispenser Conditioning MCP service identity" `
    -PasswordNeverExpires:$false -UserMayNotChangePassword

$Initializer = "C:\OperatorStaging\Initialize-DispenserMcpProtectedRoot.ps1"
powershell.exe -NoLogo -NoProfile -NonInteractive `
    -ExecutionPolicy RemoteSigned `
    -File $Initializer -RootPath $McpRoot `
    -ServiceAccount "$env:COMPUTERNAME\$McpServiceAccount" `
    -DeploymentOperatorAccount $DeploymentOperator
```

The initializer resolves both named users and rejects a disabled/non-local
identity. It rejects a service user that is a direct member of local
Administrators. It refuses any pre-existing root, protects and assigns the
fresh root to the exact operator before creating descendants, then constructs
each policy-directory DACL from scratch. Only the resolved SIDs for the exact
operator, exact service user, BUILTIN\Administrators, and SYSTEM are allowed.
The operator, Administrators, and SYSTEM receive FullControl. The service user
receives read/execute on application, dependency, virtual-environment,
configuration, and authentication trees, and Modify only on state and log
trees. It receives no FullControl or ACL-management right. Do not replace this
step with `New-Item -Force` or `icacls /grant:r`: neither proves that an
unrelated explicit ACE was removed.

Root creation and ACL assignment are two Windows filesystem operations, not one
atomic create-with-ACL call. No child is created during that brief interval,
but the parent of `DispenserConditioningMcp` must itself be an
operator-controlled protected location. Treat that parent-boundary check as a
commissioning prerequisite; this script does not claim the root had no
inherited-permission interval.

## Install and pin all reviewed artifacts

Transfer the reviewed MCP wheel, the exact commissioned
`hicube_neo_client.py`, a reviewed `py-siglent-spd3000` source archive, and this
`deployment/windows` directory through an operator-controlled channel. The MCP
wheel does not bundle either external integration. Verify all approved hashes
before installation. Record the Siglent source commit as well as the archive
hash; the v0.4.3 power contract was reviewed against commit
`0984bba67d8e5651cd2d9aa7b2c0db2d6eb694f3`.

```powershell
$McpRoot = "C:\ProgramData\DispenserConditioningMcp"
$BundleRoot = "C:\OperatorStaging\release-bundle"
$HiCubeClient = "C:\OperatorStaging\hicube_neo_client.py"
$SiglentArchive = "C:\OperatorStaging\py-siglent-spd3000-reviewed.zip"
$ExpectedHiCubeSha256 = "<approved-hicube-client-sha256>"
$ExpectedSiglentArchiveSha256 = "<approved-siglent-archive-sha256>"
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $HiCubeClient).Hash -ne `
    $ExpectedHiCubeSha256) { throw "HiCube client hash mismatch." }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $SiglentArchive).Hash -ne `
    $ExpectedSiglentArchiveSha256) { throw "Siglent archive hash mismatch." }

Copy-Item -LiteralPath $HiCubeClient `
    -Destination "$McpRoot\dependencies\hicube\hicube_neo_client.py"
Expand-Archive -LiteralPath $SiglentArchive `
    -DestinationPath "$McpRoot\dependencies\py-siglent-spd3000" -Force

$Installer = "C:\OperatorStaging\Install-DispenserMcpPython.ps1"
$Uv = "C:\OperatorStaging\uv.exe"
$PythonRuntimeRoot = "C:\Program Files\Python313"
$BasePython = "C:\Program Files\Python313\python.exe"
$RuntimeManifestCreator = `
    "C:\OperatorStaging\New-DispenserMcpPythonRuntimeManifest.ps1"
$RuntimeValidator = "C:\OperatorStaging\Test-DispenserMcpPythonRuntime.ps1"
$RuntimeManifest = "C:\OperatorApproved\python-runtime-manifest.json"
$PythonInstaller = "C:\OperatorStaging\python-3.13.x-amd64.exe"
$ReleaseValidator = "C:\OperatorStaging\release_bundle_manifest.py"
$ReleaseManifest = "$BundleRoot\release-manifest.json"

# These two checks must match values independently approved before installation.
Get-FileHash -Algorithm SHA256 -LiteralPath $PythonInstaller
$PythonInstallerSignature = Get-AuthenticodeSignature -LiteralPath $PythonInstaller
$PythonInstallerSignature.Status
$PythonInstallerSignature.SignerCertificate.Thumbprint

# Install CPython now from that approved offline installer into the dedicated,
# operator-protected runtime root using the site's reviewed installer options.
# Immediately afterward, co-record the reviewed installer identity and the
# complete tree produced by the site's procedural clean-install step:
powershell.exe -NoLogo -NoProfile -NonInteractive `
    -ExecutionPolicy RemoteSigned `
    -File $RuntimeManifestCreator `
    -RuntimeRootPath $PythonRuntimeRoot `
    -PythonPath $BasePython `
    -OutputPath $RuntimeManifest `
    -SourceInstallerPath $PythonInstaller `
    -ExpectedSourceInstallerSha256 "<approved-python-installer-sha256>" `
    -ExpectedSourceInstallerSignerThumbprint `
        "<approved-python-installer-signer-thumbprint>"

Get-FileHash -Algorithm SHA256 -LiteralPath $RuntimeManifest
# Publish/approve this runtime-manifest hash through an independent channel.

powershell.exe -NoLogo -NoProfile -NonInteractive `
    -ExecutionPolicy RemoteSigned `
    -File $Installer `
    -ReleaseBundleRootPath $BundleRoot `
    -ReleaseManifestPath $ReleaseManifest `
    -ExpectedReleaseManifestSha256 "<approved-release-manifest-sha256>" `
    -ReleaseBundleValidatorPath $ReleaseValidator `
    -ExpectedReleaseBundleValidatorSha256 `
        "<approved-release-validator-sha256>" `
    -UvPath $Uv `
    -ExpectedUvSha256 "<approved-uv-sha256>" `
    -BasePythonRuntimeRootPath $PythonRuntimeRoot `
    -BasePythonPath $BasePython `
    -PythonRuntimeManifestPath $RuntimeManifest `
    -ExpectedPythonRuntimeManifestSha256 `
        "<approved-python-runtime-manifest-sha256>" `
    -PythonRuntimeValidatorPath $RuntimeValidator `
    -ExpectedPythonRuntimeValidatorSha256 `
        "<approved-python-runtime-validator-sha256>" `
    -VenvPath "$McpRoot\venv"
```

The installer accepts only a pre-created empty protected virtual-environment
directory. Before resolving or mutating that directory, it verifies the
independently approved release-manifest hash and requires the exact declared
wheel filename/hash set. It then verifies `uv`, both validator scripts, and the
complete base-Python runtime tree against independently approved hashes. The
Python release validator reconstructs the manifest from the transferred bytes
and proves exact lock, inventory, MCP-wheel, and dependency-wheel
correspondence. Only then does the installer clear inherited Python/index
settings, create the environment without downloading Python, and perform these
dependency operations:

```text
uv --no-config pip sync --offline --no-index --find-links <wheelhouse> --require-hashes <lock>
uv --no-config pip install --offline --no-index --no-deps <verified-MCP-wheel>
```

It then runs `python -I -B -m dispenser_conditioning_mcp.deployment_inventory`
against the manifest-verified inventory. Missing, extra, or version-drifted
distributions and a platform/Python mismatch block commissioning. A failed or
interrupted install leaves the environment unapproved; discard the entire new
root under the site's controlled decommissioning process and initialize a new
root rather than reusing it.

The runtime manifest records the installer filename, hash, and signer
thumbprint and hashes every runtime file. Its creator also verifies the supplied
installer's Authenticode signature and approved identity. This co-recording
detects runtime byte drift; it does not prove that this installer caused the
runtime tree or independently establish that the supplied expected
hash/thumbprint is trustworthy. The release procedure must install into a clean
operator-controlled destination immediately before manifest creation. Obtain
the expected installer values from the site software authority, keep the
runtime root outside service/agent write access, and have an independent
reviewer approve the resulting runtime-manifest hash.

Confirm that the configured Siglent source path contains
`siglent_spd3000\__init__.py` directly below it. The MCP imports this exact
operator-selected source tree and verifies its module origin. Gateway mode uses
the standard-library gateway client; PyVISA and VXI-11 imports are lazy and are
not used by the MCP's gateway-only deployment. Do not install or enable direct
VISA/VXI/socket modes as a shortcut. If a future reviewed driver makes another
runtime dependency eager, the offline import check below must fail until that
dependency is explicitly pinned and installed.

Populate this path from the authenticated commissioned driver wheel, not a raw
Git `src` copy. The built package must include generated `_build_commit.py` and
the offline preflight requires the imported driver commit to be 40–64 lowercase
hexadecimal digits. Missing or `unknown` commit identity blocks startup before
the gateway handshake.

After Python installation and external dependency placement, run the recursive
ACL validator as an elevated operator. Run the same blocking validation again
after placing the launcher and protected profiles, and again after any later
deployment-tree change. A site may also run it under the dedicated account if
policy permits that account to inspect ACLs:

```powershell
$McpPrincipal = "$env:COMPUTERNAME\svc-dispenser-mcp"
$DeploymentOperator = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$AclValidator = "C:\OperatorStaging\Test-DispenserMcpAcl.ps1"
powershell.exe -NoLogo -NoProfile -NonInteractive `
    -ExecutionPolicy RemoteSigned `
    -File $AclValidator -RootPath $McpRoot `
    -ServiceAccount $McpPrincipal `
    -DeploymentOperatorAccount $DeploymentOperator
```

The only success output is
`Recursive deployment ACL and owner validation passed.`. It recursively rejects
reparse points, unprotected policy directories, noncanonical ACLs, unresolvable
or unapproved SIDs, duplicate or deny ACEs, excessive/insufficient rights,
missing exact operator/Administrators/SYSTEM/service access, and an owner that
is not the exact operator. Service-owned runtime objects are permitted only
below `state\unloaded-hil` and `logs`. It also rechecks that the service user is
enabled, local, and not a direct local-Administrators member. Treat any failure
as a blocking security failure. Do not add an explicit deny that can override
required group membership; correct the allow-only boundary instead.

The local-group check is deliberately limited to direct Administrators
membership. As a separate blocking commissioning check, launch a validation
session as the real service identity and record `whoami /groups` and
`whoami /priv`. In that same session, require this role check to print
`SERVICE TOKEN IS NON-ADMIN`; then have the site security operator confirm that
the effective token has no Administrators role and no unapproved enabled
privileges:

```powershell
$principal = [System.Security.Principal.WindowsPrincipal]::new(
    [System.Security.Principal.WindowsIdentity]::GetCurrent()
)
if ($principal.IsInRole(
    [System.Security.Principal.WindowsBuiltInRole]::Administrator
)) { throw "The service token is effectively administrative." }
Write-Output "SERVICE TOKEN IS NON-ADMIN"
whoami.exe /groups
whoami.exe /priv
```

This effective-token review is not replaced by the direct-membership test.

Copy the launcher to the protected app directory. Record and compare its
approved SHA-256, then remove only its download Zone.Identifier after the hash
matches. This runbook uses `RemoteSigned`, not `AllSigned`, because the supplied
launcher is not Authenticode-signed. A domain execution policy may override the
process setting; inspect site policy before commissioning. Protected ACLs and a
recorded hash are mandatory even when the script is locally trusted.

```powershell
$Launcher = "$McpRoot\app\Start-DispenserConditioningMcp.ps1"
Get-FileHash -Algorithm SHA256 -LiteralPath $Launcher
Get-ExecutionPolicy -List
Unblock-File -LiteralPath $Launcher
```

Copy each template to its
own protected profile directory, remove the `.template` suffix, and edit only
the protected copy. Keep `DISPENSER_SIGLENT_CONTROL_ENABLED = "false"` during
installation and read-only commissioning.

Run `Test-DispenserMcpAcl.ps1` again now and do not continue unless its only
output is the documented success line.

The two supplied profiles intentionally use different HTTP ports, acceptance
contexts, gateway-auth paths, and HIL state responsibilities. Never point two
processes at one PSU, and never reuse the unloaded-HIL durable state path for a
second unit. Production has no unloaded-HIL state path.

## Fast independent Streamable HTTP start

Start the process while logged on as the dedicated service account, or use
`runas` from an operator session. The launcher imports only a fixed setting
allowlist, clears inherited product settings, disables Python user-site and
startup hooks, and writes nothing to stdout before Python starts.

```powershell
$McpRoot = "C:\ProgramData\DispenserConditioningMcp"
$Launcher = "$McpRoot\app\Start-DispenserConditioningMcp.ps1"
$Profile = "$McpRoot\config\unloaded-hil\profile.psd1"
$Python = "$McpRoot\venv\Scripts\python.exe"

powershell.exe -NoLogo -NoProfile -NonInteractive `
    -ExecutionPolicy RemoteSigned `
    -File $Launcher -ProfilePath $Profile -PythonPath $Python
```

Before starting the listener, run the same launcher under the dedicated
service account with `-ValidateOnly`. This imports the installed MCP, the exact
HiCube client, and the exact Siglent source, verifies the configured local files
and builds the six-tool server, but does not construct a device client, open a
socket, parse or print the gateway token, or contact hardware.

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive `
    -ExecutionPolicy RemoteSigned `
    -File $Launcher -ProfilePath $Profile -PythonPath $Python -ValidateOnly
```

The only success output is `Offline deployment validation passed.`. Treat any
other exit as a failed installation. Run this check from a real logon or
scheduled task using the dedicated non-admin identity; an Administrator-only
success does not prove the service account ACLs.

For initial setup, an operator may keep this window open. For independent
operation, create a Task Scheduler task through the Windows UI under the
dedicated account, select **Run whether user is logged on or not**, and use:

- Program: `powershell.exe`
- Arguments: `-NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "C:\ProgramData\DispenserConditioningMcp\app\Start-DispenserConditioningMcp.ps1" -ProfilePath "C:\ProgramData\DispenserConditioningMcp\config\unloaded-hil\profile.psd1" -PythonPath "C:\ProgramData\DispenserConditioningMcp\venv\Scripts\python.exe"`
- Start in: `C:\ProgramData\DispenserConditioningMcp\app`

Use a separate task and profile for production, and never enable both against
the same PSU. Let Windows store the task credential; do not put the password on
the command line. Automatic restart is acceptable for a control-disabled
process. Do not treat automatic restart of a control-enabled process as
recovery: a crash may leave physical output energized and leaves the HIL
pending marker fail-closed. Require physical output verification, state review,
and the privileged out-of-band reset procedure before resuming.

## SSH local-forward boundary

Install OpenSSH Server using the Microsoft procedure. The Windows capability
may create or retain a broad port-22 allow rule. Before starting `sshd`, an
elevated operator must enumerate and disable every active inbound allow rule
that targets TCP/22, the `sshd` service, or `sshd.exe`; then create and verify
the one source-restricted replacement. Review the enumerated rule list before
the disabling step because this intentionally changes host firewall policy.

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
$ApprovedBridgeSource = "<approved-bridge-source-address>"
$SshdPath = "$env:WINDIR\System32\OpenSSH\sshd.exe"

function Test-PortFilterCanIncludeSsh {
    param($PortFilter)

    $protocol = [string] $PortFilter.Protocol
    if ($protocol -notin @("TCP", "6", "Any", "256")) { return $false }
    foreach ($expression in @($PortFilter.LocalPort)) {
        foreach ($part in ([string] $expression -split ",")) {
            $candidate = $part.Trim()
            if ($candidate -eq "Any" -or $candidate -eq "22") {
                return $true
            }
            if ($candidate -match '^(?<low>\d+)-(?<high>\d+)$') {
                if ([int] $Matches.low -le 22 -and 22 -le [int] $Matches.high) {
                    return $true
                }
            }
        }
    }
    return $false
}

function Get-ActiveSshAllowRule {
    $rules = Get-NetFirewallRule -PolicyStore ActiveStore `
        -Direction Inbound -Enabled True -Action Allow
    foreach ($rule in $rules) {
        $ports = @($rule | Get-NetFirewallPortFilter)
        $apps = @($rule | Get-NetFirewallApplicationFilter)
        $services = @($rule | Get-NetFirewallServiceFilter)
        $targetsPort22 = @($ports | Where-Object {
            Test-PortFilterCanIncludeSsh $_
        }).Count -gt 0
        $targetsSshd = @($apps | Where-Object {
            [string]::Equals(
                [string] $_.Program,
                $SshdPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }).Count -gt 0 -or @($services | Where-Object {
            [string]::Equals(
                [string] $_.Service,
                "sshd",
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }).Count -gt 0
        if ($targetsPort22 -or $targetsSshd) { $rule }
    }
}

$PreviousSshAllowRules = @(Get-ActiveSshAllowRule)
$PreviousSshAllowRules | Format-Table Name,DisplayName,Profile,PolicyStoreSourceType
if ($PreviousSshAllowRules.Count -gt 0) {
    $PreviousSshAllowRules | Disable-NetFirewallRule -ErrorAction Stop
}

New-NetFirewallRule -Name "DispenserMcpSshBridge" `
    -DisplayName "Dispenser MCP SSH bridge" `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow `
    -Profile Domain,Private -Program $SshdPath `
    -LocalPort 22 -RemoteAddress $ApprovedBridgeSource

$ApprovedSshRules = @(Get-ActiveSshAllowRule)
if ($ApprovedSshRules.Count -ne 1 -or
    $ApprovedSshRules[0].Name -cne "DispenserMcpSshBridge") {
    throw "The active SSH firewall allow boundary is not exact."
}
$ApprovedAddress = @(
    $ApprovedSshRules[0] | Get-NetFirewallAddressFilter
).RemoteAddress
if (@($ApprovedAddress).Count -ne 1 -or
    $ApprovedAddress[0] -cne $ApprovedBridgeSource) {
    throw "The SSH firewall source restriction is not exact."
}

Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

From the approved bridge source, verify TCP/22 succeeds. **Unauthorized-source
negative test:** from a genuinely different host/address that is not covered by
`$ApprovedBridgeSource`, require `Test-NetConnection <dedicated-host> -Port 22`
to report `TcpTestSucceeded : False`. A local/loopback test is not an adequate
negative test. If either result differs, commissioning is blocked pending an
independent effective-firewall-policy audit.

Use a separate non-agent SSH bridge account with public-key-only login. In the
operator-managed `sshd_config`, restrict that identity to local forwarding,
prohibit every shell/login/subsystem session, and permit only the selected
loopback MCP port. `MaxSessions 0` is essential here: `PermitTTY no` alone does
not block noninteractive commands. A representative `Match User` boundary is:

```text
Match User mcp-bridge
    AuthenticationMethods publickey
    PasswordAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:8001
    MaxSessions 0
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
```

Validate support and syntax against the installed Win32-OpenSSH version with
`sshd.exe -t -f <reviewed-sshd-config>`. Treat lack of `MaxSessions 0` support
as a blocking site-specific design issue; do not fall back to `PermitTTY no`.
Protect `authorized_keys` according to Microsoft's Windows ACL guidance. Test
that `ssh mcp-bridge@host whoami` and an SFTP/subsystem request both fail, while
an `ssh -N` local forward succeeds. The operator-owned tunnel supervisor, not
the AI agent, holds the SSH private key and process lifecycle:

```powershell
ssh.exe -N -T -o BatchMode=yes -o ExitOnForwardFailure=yes `
    -L 127.0.0.1:8001:127.0.0.1:8001 `
    mcp-bridge@<dedicated-host>
```

The MCP client is configured only with
`http://127.0.0.1:8001/mcp`. SSH preserves the HTTP Host header, and the server
accepts only its configured loopback Host/port in `authenticated_ssh_tunnel`
mode. Therefore client-local and backend ports must be identical. If 8001 is
occupied, choose one other unused port and update the protected MCP profile,
`PermitOpen`, tunnel listen/destination, and client URL to that same value.
Never run two profiles on one port. The client does not receive SSH credentials
or server configuration. Keep the protected server profile's trust mode at
`authenticated_ssh_tunnel` before enabling control through this boundary.

## Authenticated reverse-proxy boundary

For a stable service endpoint, keep the MCP backend on loopback and terminate
TLS plus OAuth 2.1 or mTLS in an operator-owned reverse proxy. The proxy must:

- authenticate every MCP request/connection before forwarding;
- validate token audience or client certificate identity for this MCP;
- authorize exactly the intended single writer;
- preserve or set one reviewed Host value;
- never log `Authorization`, cookies, tokens, or profile values;
- forward only to the loopback MCP path; and
- expose no server-admin, filesystem, process, or reset API to the agent.

Set the protected profile to:

```powershell
"DISPENSER_MCP_HTTP_TRUST_MODE" = "authenticated_reverse_proxy"
"DISPENSER_MCP_HTTP_ALLOWED_HOSTS" = "mcp.example.invalid"
# Optional only for a reviewed browser client:
"DISPENSER_MCP_HTTP_ALLOWED_ORIGINS" = "https://console.example.invalid"
```

Replace the example names only in the protected profile. The server still
refuses a non-loopback bind. `ALLOWED_HOSTS` is a comma-separated list of exact
Host header values, optionally with exact ports; wildcards are rejected.
Origins, when present, must be exact HTTPS origins.

## Read-only commissioning

1. Confirm the task/process runs as the dedicated account.
2. Confirm the backend listens only on loopback:

   ```powershell
   Get-NetTCPConnection -State Listen -LocalPort 8001 |
       Select-Object LocalAddress, LocalPort, OwningProcess
   ```

3. Confirm the protected profile says `control_enabled=false` and uses the
   intended test or production identity.
4. Through the authenticated bridge, call MCP `tools/list`. It must advertise
   exactly six tools and no transport, credential, path, context, reset, raw
   SCPI, discovery, or channel-selection input.
5. Call `read_vacuum_pressure` and `read_dispenser_power_state`. Validate total
   pressure provenance, identity, live mode, output, policy, durable-state
   diagnostics, and timestamps. Do not interpret pressure as activation.
6. Attempt no mutating call during this stage. The explicit disabled policy
   rejects mutation before a power-supply session opens.

There is no separate unauthenticated health endpoint. Process status, a
loopback listener check, authenticated `tools/list`, and the two read-only tools
are the commissioning checks.

## Unloaded-HIL commissioning

Use only the unloaded-HIL process/profile and its unique protected state file.
Follow the component README's supervised 1.0 V / 0.2 A sequence. Before changing
the protected profile to `control_enabled=true`:

- physically verify no dispenser or unapproved load is connected;
- allow only explicitly approved metrology wiring;
- verify both outputs off and parallel tracking out-of-band;
- verify the durable state is readable and unlatched;
- stop every other writer; and
- assign an operator who can remove power physically.

With both MCP processes stopped and both PSU outputs physically verified off,
the elevated operator must create the initial durable state exactly once:

```powershell
$Initializer = "C:\OperatorStaging\Initialize-DispenserMcpUnloadedHilState.ps1"
powershell.exe -NoLogo -NoProfile -NonInteractive `
    -ExecutionPolicy RemoteSigned -File $Initializer `
    -RootPath "C:\ProgramData\DispenserConditioningMcp" `
    -PhysicalVerification confirmed_outputs_off_and_no_unapproved_load
```

The initializer uses atomic `FileMode.CreateNew`, write-through flush, and byte
verification. It never overwrites, resets, clears, or removes a state record.
Run `Test-DispenserMcpAcl.ps1` immediately afterward. Missing, deleted, partial,
malformed, pending, or trip state denies HIL mutations before device-session
creation; a missing file is not an unlatched/reset state.

The human confirmation is fresh for one enable call. A trip, pending marker,
state read/write failure, process crash, or uncertain shutdown requires physical
verification and the separate privileged reset procedure. No MCP reset exists.

## Production commissioning

Production is a separate process instance, profile, acceptance context,
gateway-auth file, port, and reviewed compliance/current policy. A successful
test-unit run does not validate production wiring or policy. Keep production
control disabled until operators verify exact identity, parallel mode, both
outputs, fixed compliance, current ceilings, physical parallel wiring, the
absence of another writer, and a physical shutdown path. The production enable
requires fresh `confirmed_parallel_ch1` confirmation.

## Logging and restart

- Send diagnostics to stderr or a protected service log; stdout belongs to
  stdio protocol when stdio is selected.
- Never enable PowerShell transcription for the launcher or dump its process
  environment.
- Do not log gateway tokens, Authorization headers, profile bodies, device
  endpoints, auth paths, or raw exceptions.
- Rotate logs under the operator boundary and grant the AI client no log access.
- After an unexpected control-enabled exit, assume output state may be unknown.
  Physically verify shutdown before resuming. Restart does not reset durable
  HIL state.

## Rollback and uninstall

1. Stop experimentation. If a controlled call is still possible, call
   `shutdown_dispenser_power`; independently verify both outputs physically.
2. Set the protected profile back to `control_enabled=false`.
3. Stop and disable the exact Task Scheduler task or supervised process.
4. Close the operator-owned tunnel or reverse-proxy route and disable its exact
   firewall rule.
5. Archive the profile, logs, auth metadata, and HIL state under operator
   control. Do not delete or replace a pending/trip state as an uninstall
   shortcut.
6. Uninstall the reviewed package from its dedicated virtual environment only
   after confirming the resolved installation path.
7. Remove the dedicated account and directories only under the site's normal
   decommissioning and retention procedure.

Rollback cannot substitute for a physical E-stop or privileged HIL reset.
