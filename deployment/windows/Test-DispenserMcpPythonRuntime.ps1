[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $RuntimeRootPath,

    [Parameter(Mandatory = $true)]
    [string] $PythonPath,

    [Parameter(Mandatory = $true)]
    [string] $ManifestPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string] $ExpectedManifestSha256
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module -Name Microsoft.PowerShell.Utility -ErrorAction Stop

function Assert-ExactProperties {
    param([object] $Value, [string[]] $Names)

    $actual = @($Value.PSObject.Properties.Name)
    if ($actual.Count -ne $Names.Count) {
        throw "Python runtime manifest object shape is invalid."
    }
    foreach ($name in $Names) {
        if ($actual -notcontains $name) {
            throw "Python runtime manifest object shape is invalid."
        }
    }
}

function Assert-NoReparsePoint {
    param([System.IO.FileSystemInfo] $Item)

    if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "The Python runtime tree contains a forbidden reparse point."
    }
}

if (-not [System.IO.Path]::IsPathRooted($RuntimeRootPath) -or
    -not [System.IO.Path]::IsPathRooted($PythonPath) -or
    -not [System.IO.Path]::IsPathRooted($ManifestPath)) {
    throw "Python runtime validation paths must be absolute."
}
$runtimeRoot = (Resolve-Path -LiteralPath $RuntimeRootPath).Path.TrimEnd("\")
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$manifestFile = (Resolve-Path -LiteralPath $ManifestPath).Path
if (-not (Get-Item -Force -LiteralPath $runtimeRoot).PSIsContainer -or
    (Get-Item -Force -LiteralPath $python).PSIsContainer -or
    (Get-Item -Force -LiteralPath $manifestFile).PSIsContainer) {
    throw "Python runtime validation paths identify invalid objects."
}
$manifestHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifestFile).Hash
if (-not [string]::Equals(
    $manifestHash,
    $ExpectedManifestSha256,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Python runtime manifest hash does not match the approved value."
}

try {
    $manifest = Get-Content -Raw -LiteralPath $manifestFile | ConvertFrom-Json
}
catch {
    throw "Python runtime manifest could not be parsed."
}
Assert-ExactProperties $manifest @(
    "schema_version",
    "kind",
    "source_installer",
    "target",
    "executable",
    "files"
)
Assert-ExactProperties $manifest.source_installer @(
    "filename",
    "sha256",
    "authenticode_signer_thumbprint"
)
Assert-ExactProperties $manifest.target @(
    "implementation",
    "version",
    "platform",
    "machine",
    "pointer_bits"
)
if ($manifest.schema_version -isnot [int] -or $manifest.schema_version -ne 1 -or
    $manifest.kind -ne "approved_cpython_runtime_tree" -or
    $manifest.source_installer.filename -isnot [string] -or
    [System.IO.Path]::GetFileName($manifest.source_installer.filename) -cne `
        $manifest.source_installer.filename -or
    $manifest.source_installer.sha256 -notmatch "^[0-9a-f]{64}$" -or
    $manifest.source_installer.authenticode_signer_thumbprint -notmatch `
        "^[0-9a-f]{40}([0-9a-f]{24})?$" -or
    $manifest.target.implementation -ne "CPython" -or
    $manifest.target.platform -ne "win32" -or
    $manifest.target.machine -ne "AMD64" -or
    $manifest.target.pointer_bits -ne 64 -or
    ([string] $manifest.target.version) -notmatch "^3\.13\.[0-9]+$") {
    throw "Python runtime manifest metadata is invalid."
}
if ($manifest.executable -isnot [string] -or
    [System.IO.Path]::IsPathRooted($manifest.executable) -or
    $manifest.executable.Contains("..") -or
    $manifest.executable.Contains(":")) {
    throw "Python runtime manifest executable is invalid."
}
$manifestPython = [System.IO.Path]::GetFullPath((
    Join-Path $runtimeRoot ([string] $manifest.executable).Replace("/", "\")
))
if (-not [string]::Equals(
    $manifestPython,
    $python,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "PythonPath does not match the approved runtime manifest."
}

$items = @(
    Get-Item -Force -LiteralPath $runtimeRoot
    Get-ChildItem -Force -Recurse -LiteralPath $runtimeRoot
)
foreach ($item in $items) {
    Assert-NoReparsePoint $item
}
$files = @($items | Where-Object { -not $_.PSIsContainer })
$actualByPath = @{}
foreach ($file in $files) {
    $relative = $file.FullName.Substring($runtimeRoot.Length + 1).Replace("\", "/")
    if ($actualByPath.ContainsKey($relative)) {
        throw "Python runtime tree contains a duplicate relative path."
    }
    $actualByPath[$relative] = $file.FullName
}
if ($manifest.files -isnot [array] -or $manifest.files.Count -ne $actualByPath.Count) {
    throw "Python runtime file set does not match the approved manifest."
}
$seen = @{}
foreach ($record in $manifest.files) {
    Assert-ExactProperties $record @("path", "sha256")
    if ($record.path -isnot [string] -or
        [System.IO.Path]::IsPathRooted($record.path) -or
        $record.path.Contains("..") -or
        $record.path.Contains(":") -or
        $record.sha256 -notmatch "^[0-9a-f]{64}$" -or
        $seen.ContainsKey($record.path) -or
        -not $actualByPath.ContainsKey($record.path)) {
        throw "Python runtime file record is invalid."
    }
    $seen[$record.path] = $true
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (
        $actualByPath[$record.path]
    )).Hash
    if (-not [string]::Equals(
        $actualHash,
        $record.sha256,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Python runtime file hash does not match the approved manifest."
    }
}

[System.Environment]::SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1", "Process")
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
foreach ($name in @("implementation", "version", "platform", "machine", "pointer_bits")) {
    if ($identity.$name -ne $manifest.target.$name) {
        throw "Python runtime identity does not match the approved manifest."
    }
}

Write-Output "Python runtime provenance validation passed."
