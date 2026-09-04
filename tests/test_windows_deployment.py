from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
DEPLOYMENT_ROOT = PROJECT_ROOT / "deployment" / "windows"
LAUNCHER = DEPLOYMENT_ROOT / "Start-DispenserConditioningMcp.ps1"
INSTALLER = DEPLOYMENT_ROOT / "Install-DispenserMcpPython.ps1"
ROOT_INITIALIZER = DEPLOYMENT_ROOT / "Initialize-DispenserMcpProtectedRoot.ps1"
HIL_STATE_INITIALIZER = DEPLOYMENT_ROOT / "Initialize-DispenserMcpUnloadedHilState.ps1"
ACL_VALIDATOR = DEPLOYMENT_ROOT / "Test-DispenserMcpAcl.ps1"
RUNTIME_MANIFEST_CREATOR = DEPLOYMENT_ROOT / "New-DispenserMcpPythonRuntimeManifest.ps1"
RUNTIME_VALIDATOR = DEPLOYMENT_ROOT / "Test-DispenserMcpPythonRuntime.ps1"
DEPENDENCY_LOCK = DEPLOYMENT_ROOT / "python-dependencies.lock.txt"
RUNTIME_INVENTORY = DEPLOYMENT_ROOT / "python-runtime-inventory.json"
UNLOADED_PROFILE = DEPLOYMENT_ROOT / "profiles" / "unloaded-hil.psd1.template"
PRODUCTION_PROFILE = DEPLOYMENT_ROOT / "profiles" / "production-dispenser.psd1.template"


def test_deployment_templates_are_no_secret_control_disabled_profiles() -> None:
    unloaded = UNLOADED_PROFILE.read_text(encoding="utf-8")
    production = PRODUCTION_PROFILE.read_text(encoding="utf-8")

    for profile in (unloaded, production):
        assert '"DISPENSER_MCP_TRANSPORT" = "streamable-http"' in profile
        assert '"DISPENSER_MCP_HTTP_BIND_HOST" = "127.0.0.1"' in profile
        assert '"DISPENSER_SIGLENT_CONTROL_ENABLED" = "false"' in profile
        assert "token =" not in profile
        assert "192.168." not in profile
    assert '"DISPENSER_MCP_HTTP_PORT" = "8001"' in unloaded
    assert '"DISPENSER_MCP_HTTP_PORT" = "8002"' in production
    assert '"DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT" = "unloaded_hil"' in unloaded
    assert (
        '"DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT" = "production_dispenser"' in production
    )
    assert "DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE" in unloaded
    assert "DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE" not in production


def test_launcher_rejects_unknown_profile_setting_before_python(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "invalid.psd1"
    profile.write_text(
        """
@{
    "DISPENSER_MCP_TRANSPORT" = "stdio"
    "DISPENSER_SIGLENT_CONTROL_ENABLED" = "false"
    "DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT" = "production_dispenser"
    "UNSUPPORTED_AGENT_SETTING" = "denied"
}
""".lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "-ProfilePath",
            str(profile),
            "-PythonPath",
            sys.executable,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "unsupported setting" in result.stderr


def test_launcher_clears_inherited_configuration_and_python_hooks() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")

    assert "Import-PowerShellDataFile" in launcher
    assert "unsupported setting" in launcher
    assert 'SetEnvironmentVariable($name, $null, "Process")' in launcher
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONSTARTUP",
        "PYTHONNOUSERSITE",
    ):
        assert name in launcher
    assert "-I -m dispenser_conditioning_mcp" in launcher


def test_runbook_uses_runnable_hash_pinned_acl_boundary() -> None:
    runbook = (DEPLOYMENT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "ExecutionPolicy AllSigned" not in runbook
    assert "ExecutionPolicy RemoteSigned" in runbook
    assert "Get-FileHash -Algorithm SHA256 -LiteralPath $Launcher" in runbook
    assert "Initialize-DispenserMcpProtectedRoot.ps1" in runbook
    assert "Initialize-DispenserMcpUnloadedHilState.ps1" in runbook
    assert "Test-DispenserMcpAcl.ps1" in runbook
    assert "Install-DispenserMcpPython.ps1" in runbook
    assert "release-manifest.json" in runbook
    assert "ExpectedReleaseManifestSha256" in runbook
    assert "New-DispenserMcpPythonRuntimeManifest.ps1" in runbook
    assert "ExpectedPythonRuntimeManifestSha256" in runbook
    assert "--dest C:\\ReleaseStaging\\release-bundle\\wheelhouse" in runbook
    assert "$PreviousSshAllowRules | Disable-NetFirewallRule" in runbook
    assert "Get-NetFirewallPortFilter" in runbook
    assert "Get-NetFirewallApplicationFilter" in runbook
    assert "Get-NetFirewallServiceFilter" in runbook
    assert "Test-PortFilterCanIncludeSsh" in runbook
    assert '-split ","' in runbook
    assert "Unauthorized-source\nnegative test" in runbook
    assert "MaxSessions 0" in runbook
    assert "`PermitTTY no` alone does" in runbook
    assert "not block noninteractive commands" in runbook
    assert "-L 127.0.0.1:8001:127.0.0.1:8001" in runbook
    assert "client-local and backend ports must be identical" in runbook
    assert "python-runtime-inventory.json" in runbook
    assert "RootPath must not already exist" not in runbook
    assert "New-Item -ItemType Directory -Force" not in runbook
    assert "hicube_neo_client.py" in runbook
    assert "py-siglent-spd3000-reviewed.zip" in runbook
    assert "-ValidateOnly" in runbook


def test_windows_hil_initializer_is_atomic_and_has_no_reset_surface() -> None:
    initializer = HIL_STATE_INITIALIZER.read_text(encoding="utf-8")

    assert "confirmed_outputs_off_and_no_unapproved_load" in initializer
    assert "[System.IO.FileMode]::CreateNew" in initializer
    assert "[System.IO.FileOptions]::WriteThrough" in initializer
    assert "$stream.Flush($true)" in initializer
    assert 'record_type = "initialized_state"' in initializer
    assert "schema_version = 1" in initializer
    assert "Remove-Item" not in initializer
    assert "Delete(" not in initializer
    assert "Reset" not in initializer


def test_dependency_lock_is_exact_and_hash_locked() -> None:
    lock = DEPENDENCY_LOCK.read_text(encoding="utf-8")
    requirement_lines = [
        line
        for line in lock.splitlines()
        if line and not line[0].isspace() and not line.startswith("#")
    ]

    assert requirement_lines
    for line in requirement_lines:
        assert re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[^;\\ ]+", line)
        assert line.endswith("\\")
    assert lock.count("--hash=sha256:") >= len(requirement_lines)
    assert "-e " not in lock
    assert ";" not in lock
    assert "httpx2-jsfetch" not in lock


def test_offline_installer_enforces_hashes_no_index_and_exact_inventory() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    for required in (
        '"sync"',
        '"--offline"',
        '"--no-index"',
        '"--find-links"',
        '"--require-hashes"',
        '"--no-deps"',
        '"--no-config"',
        "ExpectedUvSha256",
        "ExpectedReleaseManifestSha256",
        "ExpectedReleaseBundleValidatorSha256",
        "ExpectedPythonRuntimeManifestSha256",
        "ExpectedPythonRuntimeValidatorSha256",
        "Release bundle semantic validation failed",
        "Python runtime validation failed",
        "dispenser_conditioning_mcp.deployment_inventory",
    ):
        assert required in installer
    assert "pip install --python" not in installer
    assert RUNTIME_INVENTORY.is_file()


def test_installer_uses_script_output_not_stale_native_exit_code() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    runtime_start = installer.index("$runtimeValidationOutput")
    release_start = installer.index("$verificationJson", runtime_start)
    runtime_segment = installer[runtime_start:release_start]

    assert "-ErrorAction Stop" in runtime_segment
    assert "Python runtime provenance validation passed." in runtime_segment
    assert "$LASTEXITCODE" not in runtime_segment


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows deployment contract")
def test_offline_installer_rejects_incomplete_wheelhouse_before_install(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    wheelhouse = bundle / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    expected_wheel = b"required dependency wheel"
    manifest = {
        "schema_version": 1,
        "kind": "dispenser_mcp_windows_python_payload",
        "target": {
            "platform": "win32",
            "machine": "AMD64",
            "python_major": 3,
            "python_minor": 13,
        },
        "dependency_lock": {"file": "lock.txt", "sha256": "0" * 64},
        "runtime_inventory": {"file": "inventory.json", "sha256": "1" * 64},
        "mcp_wheel": {"file": "mcp.whl", "sha256": "2" * 64},
        "wheelhouse": [
            {
                "file": "missing-1.0-py3-none-any.whl",
                "distribution": "missing",
                "version": "1.0",
                "sha256": hashlib.sha256(expected_wheel).hexdigest(),
            }
        ],
    }
    manifest_path = bundle / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    venv = tmp_path / "venv"
    venv.mkdir()
    sentinel = venv / "must-remain.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    nonexistent = tmp_path / "must-not-be-resolved"

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "-ReleaseBundleRootPath",
            str(bundle),
            "-ReleaseManifestPath",
            str(manifest_path),
            "-ExpectedReleaseManifestSha256",
            _sha256(manifest_path),
            "-ReleaseBundleValidatorPath",
            str(nonexistent),
            "-ExpectedReleaseBundleValidatorSha256",
            "3" * 64,
            "-UvPath",
            str(nonexistent),
            "-ExpectedUvSha256",
            "4" * 64,
            "-BasePythonRuntimeRootPath",
            str(nonexistent),
            "-BasePythonPath",
            str(nonexistent),
            "-PythonRuntimeManifestPath",
            str(nonexistent),
            "-ExpectedPythonRuntimeManifestSha256",
            "5" * 64,
            "-PythonRuntimeValidatorPath",
            str(nonexistent),
            "-ExpectedPythonRuntimeValidatorSha256",
            "6" * 64,
            "-VenvPath",
            str(venv),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert "wheelhouse is incomplete" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert list(venv.iterdir()) == [sentinel]


def _windows_identity() -> str:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def _signed_windows_binary() -> tuple[Path, str]:
    command = r"""
Import-Module "$PSHOME\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
$path = (Get-Command powershell.exe).Source
$signature = Get-AuthenticodeSignature -LiteralPath $path
[pscustomobject]@{
    path = $path
    status = [string] $signature.Status
    status_message = [string] $signature.StatusMessage
    error = if ($Error.Count) { [string] $Error[0] } else { "" }
    thumbprint = $signature.SignerCertificate.Thumbprint
} | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(command.encode("utf-16le")).decode("ascii"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    raw = json.loads(result.stdout)
    assert raw["status"] == "Valid", raw
    assert raw["thumbprint"]
    return Path(raw["path"]), raw["thumbprint"]


def _local_service_identity() -> str:
    command = r"""
$administrators = @(
    Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { $_.SID.Value }
)
$candidate = Get-LocalUser | Where-Object {
    $_.Enabled -and $administrators -notcontains $_.SID.Value
} | Select-Object -First 1
if ($null -eq $candidate) { exit 3 }
Write-Output ("{0}\{1}" -f $env:COMPUTERNAME, $candidate.Name)
"""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 3:
        pytest.skip("No enabled non-administrative local test identity is available")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _local_administrator_identity() -> str:
    command = r"""
$current = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$memberSids = @(
    Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { $_.SID.Value }
)
if ($memberSids -notcontains $current.User.Value) { exit 3 }
Write-Output $current.Name
"""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 3:
        pytest.skip("No enabled local Administrators member is available")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _restore_test_acl(root: Path, identity: str) -> None:
    subprocess.run(
        ["icacls.exe", str(root), "/grant:r", f"{identity}:(OI)(CI)F", "/T", "/C"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL contract")
def test_acl_initializer_requires_fresh_root(tmp_path: Path) -> None:
    root = tmp_path / "DispenserConditioningMcp"
    root.mkdir()

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT_INITIALIZER),
            "-RootPath",
            str(root),
            "-ServiceAccount",
            _local_service_identity(),
            "-DeploymentOperatorAccount",
            _windows_identity(),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert "must not already exist" in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL contract")
def test_acl_initializer_rejects_local_administrators_member_before_root_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "DispenserConditioningMcp"

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT_INITIALIZER),
            "-RootPath",
            str(root),
            "-ServiceAccount",
            _local_administrator_identity(),
            "-DeploymentOperatorAccount",
            _local_service_identity(),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert "must not be a member of local Administrators" in result.stderr
    assert not root.exists()


def test_acl_initializer_protects_root_before_creating_descendants() -> None:
    initializer = ROOT_INITIALIZER.read_text(encoding="utf-8")
    validator = ACL_VALIDATOR.read_text(encoding="utf-8")
    root_create = initializer.index(
        "[void](New-Item -ItemType Directory -Path $RootPath)"
    )
    root_protect = initializer.index("Set-ExactDirectoryAcl", root_create)
    child_loop = initializer.index("foreach ($relative in $relativeDirectories)")

    assert root_create < root_protect < child_loop
    assert "$acl.SetOwner($OperatorSid)" in initializer
    assert "$acl.GetOwner(" in validator
    assert "Deployment item owner is not approved for this path." in validator
    assert "$rule.IsInherited" in validator
    assert "$rule.InheritanceFlags" in validator
    assert "$rule.PropagationFlags" in validator


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL contract")
def test_acl_validator_rejects_wrong_inheritance_flags(tmp_path: Path) -> None:
    root = tmp_path / "DispenserConditioningMcp"
    operator = _windows_identity()
    service = _local_service_identity()
    alter_service_ace = r"""
$path = Join-Path $env:ACL_TEST_ROOT 'app'
$serviceSid = ([System.Security.Principal.NTAccount] $env:ACL_TEST_SERVICE).Translate(
    [System.Security.Principal.SecurityIdentifier]
)
$acl = [System.IO.Directory]::GetAccessControl($path)
$existing = @($acl.Access | Where-Object {
    $_.IdentityReference.Translate(
        [System.Security.Principal.SecurityIdentifier]
    ).Value -eq $serviceSid.Value
})
if ($existing.Count -ne 1) { throw 'Expected one service ACE.' }
$acl.RemoveAccessRuleSpecific($existing[0])
$wrong = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $serviceSid,
    [System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
    [System.Security.AccessControl.InheritanceFlags]::None,
    [System.Security.AccessControl.PropagationFlags]::None,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.AddAccessRule($wrong)
[System.IO.Directory]::SetAccessControl($path, $acl)
"""
    try:
        initialized = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT_INITIALIZER),
                "-RootPath",
                str(root),
                "-ServiceAccount",
                service,
                "-DeploymentOperatorAccount",
                operator,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert initialized.returncode == 0, initialized.stderr
        environment = dict(os.environ)
        environment["ACL_TEST_ROOT"] = str(root)
        environment["ACL_TEST_SERVICE"] = service
        altered = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                alter_service_ace,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
        assert altered.returncode == 0, altered.stderr
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ACL_VALIDATOR),
                "-RootPath",
                str(root),
                "-ServiceAccount",
                service,
                "-DeploymentOperatorAccount",
                operator,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode != 0
        assert "inheritance and propagation flags are not exact" in result.stderr
    finally:
        _restore_test_acl(root, operator)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL contract")
def test_acl_validator_rejects_surviving_explicit_users_ace(tmp_path: Path) -> None:
    root = tmp_path / "DispenserConditioningMcp"
    operator = _windows_identity()
    service = _local_service_identity()
    users_sid = "S-1-5-32-545"
    add_users_ace = (
        "$acl=[System.IO.Directory]::GetAccessControl($env:ACL_TEST_ROOT);"
        "$sid=New-Object System.Security.Principal.SecurityIdentifier("
        "$env:ACL_TEST_USERS_SID);"
        "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule("
        "$sid,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow');"
        "$acl.AddAccessRule($rule);"
        "[System.IO.Directory]::SetAccessControl($env:ACL_TEST_ROOT,$acl)"
    )
    try:
        initialized = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT_INITIALIZER),
                "-RootPath",
                str(root),
                "-ServiceAccount",
                service,
                "-DeploymentOperatorAccount",
                operator,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert initialized.returncode == 0, initialized.stderr
        environment = dict(os.environ)
        environment["ACL_TEST_ROOT"] = str(root)
        environment["ACL_TEST_USERS_SID"] = users_sid
        subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                add_users_ace,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ACL_VALIDATOR),
                "-RootPath",
                str(root),
                "-ServiceAccount",
                service,
                "-DeploymentOperatorAccount",
                operator,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode != 0
        assert "unapproved identity" in result.stderr
    finally:
        _restore_test_acl(root, operator)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL contract")
def test_fresh_protected_root_passes_recursive_acl_validation(tmp_path: Path) -> None:
    root = tmp_path / "DispenserConditioningMcp"
    operator = _windows_identity()
    service = _local_service_identity()
    try:
        initialized = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT_INITIALIZER),
                "-RootPath",
                str(root),
                "-ServiceAccount",
                service,
                "-DeploymentOperatorAccount",
                operator,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert initialized.returncode == 0, initialized.stderr

        state_file = root / "state" / "unloaded-hil" / "operation-state.json"
        state_file.write_text("{}", encoding="utf-8")
        validated = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ACL_VALIDATOR),
                "-RootPath",
                str(root),
                "-ServiceAccount",
                service,
                "-DeploymentOperatorAccount",
                operator,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert validated.returncode == 0, validated.stderr
        assert (
            validated.stdout.strip()
            == "Recursive deployment ACL and owner validation passed."
        )
    finally:
        _restore_test_acl(root, operator)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime contract")
def test_python_runtime_manifest_covers_exact_runtime_tree(tmp_path: Path) -> None:
    base_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    runtime_root = base_python.parent
    manifest = tmp_path / "python-runtime-manifest.json"
    source_installer, signer_thumbprint = _signed_windows_binary()
    created = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUNTIME_MANIFEST_CREATOR),
            "-RuntimeRootPath",
            str(runtime_root),
            "-PythonPath",
            str(base_python),
            "-OutputPath",
            str(manifest),
            "-SourceInstallerPath",
            str(source_installer),
            "-ExpectedSourceInstallerSha256",
            _sha256(source_installer),
            "-ExpectedSourceInstallerSignerThumbprint",
            signer_thumbprint,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert created.returncode == 0, created.stderr

    validated = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUNTIME_VALIDATOR),
            "-RuntimeRootPath",
            str(runtime_root),
            "-PythonPath",
            str(base_python),
            "-ManifestPath",
            str(manifest),
            "-ExpectedManifestSha256",
            _sha256(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert validated.returncode == 0, validated.stderr
    assert validated.stdout.strip() == "Python runtime provenance validation passed."

    raw = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(raw["files"]) > 1
    raw["files"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    rejected = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RUNTIME_VALIDATOR),
            "-RuntimeRootPath",
            str(runtime_root),
            "-PythonPath",
            str(base_python),
            "-ManifestPath",
            str(manifest),
            "-ExpectedManifestSha256",
            _sha256(manifest),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert rejected.returncode != 0
    assert "file hash does not match" in rejected.stderr


def test_launcher_offline_validation_imports_protected_layout(
    tmp_path: Path,
) -> None:
    client_file = tmp_path / "hicube_neo_client.py"
    client_file.write_text(
        "class HiCubeNeoClient:\n    pass\n",
        encoding="utf-8",
    )
    driver_src = tmp_path / "driver" / "src"
    driver_package = driver_src / "siglent_spd3000"
    driver_package.mkdir(parents=True)
    (driver_package / "__init__.py").write_text(
        "class SPD3000:\n    pass\n"
        "class Channel:\n    pass\n"
        "class ConnectionType:\n    pass\n"
        "class OperatingMode:\n    pass\n"
        "class OutputState:\n    pass\n"
        "def load_gateway_auth(*args, **kwargs):\n    return 'fixture'\n",
        encoding="utf-8",
    )
    auth_file = tmp_path / "gateway-auth.toml"
    auth_file.write_text('token = "offline-placeholder"\n', encoding="utf-8")
    profile = tmp_path / "offline.psd1"
    profile.write_text(
        f"""
@{{
    "DISPENSER_MCP_TRANSPORT" = "stdio"
    "DISPENSER_HICUBE_CLIENT_FILE" = "{client_file}"
    "DISPENSER_HICUBE_HOST" = "offline.test"
    "DISPENSER_HICUBE_PORT" = "4840"
    "DISPENSER_HICUBE_TIMEOUT_S" = "1.0"
    "DISPENSER_SIGLENT_DRIVER_SRC" = "{driver_src}"
    "DISPENSER_SIGLENT_CONNECTION" = "gateway"
    "DISPENSER_SIGLENT_IDENTIFIER" = "offline.test:8765"
    "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE" = "{auth_file}"
    "DISPENSER_SIGLENT_TIMEOUT_S" = "1.0"
    "DISPENSER_SIGLENT_MIN_COMMAND_INTERVAL_MS" = "100.0"
    "DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT" = "production_dispenser"
    "DISPENSER_SIGLENT_TOPOLOGY" = "parallel_ch1"
    "DISPENSER_SIGLENT_CHANNEL" = "CH1"
    "DISPENSER_SIGLENT_EXPECTED_MODEL" = "SPD3303X"
    "DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER" = "SPD-OFFLINE"
    "DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V" = "1.0"
    "DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A" = "0.2"
    "DISPENSER_SIGLENT_UPWARD_STEP_A" = "0.2"
    "DISPENSER_SIGLENT_CONTROL_ENABLED" = "false"
}}
""".lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(LAUNCHER),
            "-ProfilePath",
            str(profile),
            "-PythonPath",
            sys.executable,
            "-ValidateOnly",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Offline deployment validation passed."
    assert result.stderr == ""
