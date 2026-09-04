[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $RuntimeRootPath,

    [Parameter(Mandatory = $true)]
    [string] $PythonPath,

    [Parameter(Mandatory = $true)]
    [string] $OutputPath,

    [Parameter(Mandatory = $true)]
    [string] $SourceInstallerPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string] $ExpectedSourceInstallerSha256,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?$")]
    [string] $ExpectedSourceInstallerSignerThumbprint
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module -Name Microsoft.PowerShell.Utility -ErrorAction Stop
Import-Module `
    -Name "$PSHOME\Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1" `
    -ErrorAction Stop

function Assert-NoReparsePoint {
    param([System.IO.FileSystemInfo] $Item)

    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "The Python runtime tree contains a forbidden reparse point."
    }
}

if (-not [System.IO.Path]::IsPathRooted($RuntimeRootPath) -or
    -not [System.IO.Path]::IsPathRooted($PythonPath) -or
    -not [System.IO.Path]::IsPathRooted($OutputPath) -or
    -not [System.IO.Path]::IsPathRooted($SourceInstallerPath)) {
    throw "Python runtime manifest paths must be absolute."
}
$runtimeRoot = (Resolve-Path -LiteralPath $RuntimeRootPath).Path.TrimEnd("\")
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$sourceInstaller = (Resolve-Path -LiteralPath $SourceInstallerPath).Path
if (-not (Get-Item -Force -LiteralPath $runtimeRoot).PSIsContainer -or
    (Get-Item -Force -LiteralPath $python).PSIsContainer -or
    (Get-Item -Force -LiteralPath $sourceInstaller).PSIsContainer) {
    throw "Python runtime manifest paths identify invalid objects."
}
if (-not $python.StartsWith($runtimeRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "PythonPath must be inside RuntimeRootPath."
}
$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
if ($outputFullPath.StartsWith($runtimeRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputPath must be outside RuntimeRootPath."
}
if (Test-Path -LiteralPath $outputFullPath) {
    throw "OutputPath must not already exist."
}

$installerItem = Get-Item -Force -LiteralPath $sourceInstaller
Assert-NoReparsePoint $installerItem
$actualInstallerHash = (Get-FileHash `
    -Algorithm SHA256 `
    -LiteralPath $sourceInstaller).Hash
if (-not [string]::Equals(
    $actualInstallerHash,
    $ExpectedSourceInstallerSha256,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Source installer hash does not match the approved value."
}
$signature = Get-AuthenticodeSignature -LiteralPath $sourceInstaller
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
    $null -eq $signature.SignerCertificate -or
    -not [string]::Equals(
        $signature.SignerCertificate.Thumbprint,
        $ExpectedSourceInstallerSignerThumbprint,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Source installer Authenticode identity is not approved."
}
$outputParent = [System.IO.Path]::GetDirectoryName($outputFullPath)
if (-not $outputParent -or -not [System.IO.Directory]::Exists($outputParent)) {
    throw "OutputPath parent directory is unavailable."
}

$items = @(
    Get-Item -Force -LiteralPath $runtimeRoot
    Get-ChildItem -Force -Recurse -LiteralPath $runtimeRoot
)
foreach ($item in $items) {
    Assert-NoReparsePoint $item
}
$files = @($items | Where-Object { -not $_.PSIsContainer })
if ($files.Count -eq 0) {
    throw "Python runtime tree is empty."
}

$identityJson = & $python -I -B -c @"
import json, platform, sys
print(json.dumps({
    'implementation': platform.python_implementation(),
    'version': platform.python_version(),
    'platform': sys.platform,
    'machine': platform.machine(),
    'pointer_bits': 64 if sys.maxsize > 2**32 else 32,
}, separators=(',', ':'), sort_keys=True))
"@
if ($LASTEXITCODE -ne 0) {
    throw "Python runtime identity query failed."
}
$identity = $identityJson | ConvertFrom-Json
if ($identity.implementation -ne "CPython" -or
    $identity.platform -ne "win32" -or
    $identity.machine -ne "AMD64" -or
    $identity.pointer_bits -ne 64 -or
    -not ([string] $identity.version).StartsWith("3.13.", [System.StringComparison]::Ordinal)) {
    throw "Python runtime identity is unsupported."
}

[string[]] $relativePaths = @(
    $files | ForEach-Object {
        $_.FullName.Substring($runtimeRoot.Length + 1).Replace("\", "/")
    }
)
[Array]::Sort($relativePaths, [System.StringComparer]::Ordinal)
$records = @(
    foreach ($relative in $relativePaths) {
        $nativeRelative = $relative.Replace("/", "\")
        [ordered]@{
            path = $relative
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (
                Join-Path $runtimeRoot $nativeRelative
            )).Hash.ToLowerInvariant()
        }
    }
)
$manifest = [ordered]@{
    schema_version = 1
    kind = "approved_cpython_runtime_tree"
    source_installer = [ordered]@{
        filename = [System.IO.Path]::GetFileName($sourceInstaller)
        sha256 = $ExpectedSourceInstallerSha256.ToLowerInvariant()
        authenticode_signer_thumbprint = `
            $ExpectedSourceInstallerSignerThumbprint.ToLowerInvariant()
    }
    target = [ordered]@{
        implementation = "CPython"
        version = [string] $identity.version
        platform = "win32"
        machine = "AMD64"
        pointer_bits = 64
    }
    executable = $python.Substring($runtimeRoot.Length + 1).Replace("\", "/")
    files = $records
}
$json = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
    $outputFullPath,
    $json + "`n",
    (New-Object System.Text.UTF8Encoding($false))
)
Write-Output "Python runtime manifest creation passed."
