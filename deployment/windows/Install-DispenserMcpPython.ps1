[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ReleaseBundleRootPath,

    [Parameter(Mandatory = $true)]
    [string] $ReleaseManifestPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string] $ExpectedReleaseManifestSha256,

    [Parameter(Mandatory = $true)]
    [string] $ReleaseBundleValidatorPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string] $ExpectedReleaseBundleValidatorSha256,

    [Parameter(Mandatory = $true)]
    [string] $UvPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string] $ExpectedUvSha256,

    [Parameter(Mandatory = $true)]
    [string] $BasePythonRuntimeRootPath,

    [Parameter(Mandatory = $true)]
    [string] $BasePythonPath,

    [Parameter(Mandatory = $true)]
    [string] $PythonRuntimeManifestPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string] $ExpectedPythonRuntimeManifestSha256,

    [Parameter(Mandatory = $true)]
    [string] $PythonRuntimeValidatorPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9A-Fa-f]{64}$")]
    [string] $ExpectedPythonRuntimeValidatorSha256,

    [Parameter(Mandatory = $true)]
    [string] $VenvPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module -Name Microsoft.PowerShell.Utility -ErrorAction Stop

function Resolve-DeploymentFile {
    param([string] $Path, [string] $Label)

    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "$Label must be an absolute path."
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $item = Get-Item -Force -LiteralPath $resolved
    if ($item.PSIsContainer) {
        throw "$Label must identify a file."
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a reparse point."
    }
    return $resolved
}

function Resolve-DeploymentDirectory {
    param([string] $Path, [string] $Label)

    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "$Label must be an absolute path."
    }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $item = Get-Item -Force -LiteralPath $resolved
    if (-not $item.PSIsContainer) {
        throw "$Label must identify a directory."
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a reparse point."
    }
    return $resolved
}

function Assert-FileHash {
    param([string] $Path, [string] $Expected, [string] $Label)

    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    if (-not [string]::Equals(
        $actual,
        $Expected,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Label hash does not match the reviewed value."
    }
}

function Assert-ExactObjectProperties {
    param([object] $Object, [string[]] $Names, [string] $Label)

    if ($null -eq $Object) {
        throw "$Label is missing."
    }
    $actual = @($Object.PSObject.Properties.Name | Sort-Object)
    $expected = @($Names | Sort-Object)
    if (($actual -join "`n") -cne ($expected -join "`n")) {
        throw "$Label has an invalid structure."
    }
}

function Invoke-Checked {
    param([string] $Executable, [string[]] $Arguments, [string] $Failure)

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $Failure
    }
}

# This deliberately limited preflight authenticates the manifest and proves the
# exact wheelhouse byte set before VenvPath is resolved or changed. The separately
# authenticated Python validator performs the deeper lock/inventory correspondence
# check after the base runtime itself has been verified.
$resolvedBundle = Resolve-DeploymentDirectory `
    -Path $ReleaseBundleRootPath `
    -Label "ReleaseBundleRootPath"
$resolvedManifest = Resolve-DeploymentFile `
    -Path $ReleaseManifestPath `
    -Label "ReleaseManifestPath"
Assert-FileHash `
    $resolvedManifest `
    $ExpectedReleaseManifestSha256 `
    "release bundle manifest"

try {
    $manifest = Get-Content -Raw -LiteralPath $resolvedManifest | ConvertFrom-Json
}
catch {
    throw "Release bundle manifest is not valid JSON."
}
Assert-ExactObjectProperties $manifest @(
    "schema_version",
    "kind",
    "target",
    "dependency_lock",
    "runtime_inventory",
    "mcp_wheel",
    "wheelhouse"
) "release bundle manifest"
if ($manifest.schema_version -ne 1 -or
    $manifest.kind -cne "dispenser_mcp_windows_python_payload") {
    throw "Release bundle manifest identity is invalid."
}
$expectedWheelhouse = @($manifest.wheelhouse)
if ($expectedWheelhouse.Count -eq 0) {
    throw "Release bundle manifest must contain a nonempty wheelhouse."
}
$wheelhousePath = Join-Path $resolvedBundle "wheelhouse"
if (-not [System.IO.Directory]::Exists($wheelhousePath)) {
    throw "Release bundle wheelhouse is incomplete."
}
$wheelhouseItem = Get-Item -Force -LiteralPath $wheelhousePath
if (($wheelhouseItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Release bundle wheelhouse must not be a reparse point."
}
$actualWheelFiles = @(Get-ChildItem -Force -LiteralPath $wheelhousePath)
if ($actualWheelFiles.Count -ne $expectedWheelhouse.Count) {
    throw "Release bundle wheelhouse is incomplete or contains extra entries."
}
$expectedByFile = @{}
foreach ($record in $expectedWheelhouse) {
    Assert-ExactObjectProperties $record @(
        "file",
        "distribution",
        "version",
        "sha256"
    ) "wheelhouse manifest entry"
    $filename = [string] $record.file
    if ([System.IO.Path]::GetFileName($filename) -cne $filename -or
        -not $filename.EndsWith(".whl", [System.StringComparison]::Ordinal)) {
        throw "Wheelhouse manifest contains an invalid filename."
    }
    if ($expectedByFile.ContainsKey($filename)) {
        throw "Wheelhouse manifest contains a duplicate filename."
    }
    $expectedByFile.Add($filename, [string] $record.sha256)
}
foreach ($artifact in $actualWheelFiles) {
    if ($artifact.PSIsContainer -or
        ($artifact.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not $expectedByFile.ContainsKey($artifact.Name)) {
        throw "Release bundle wheelhouse contains an unapproved entry."
    }
    Assert-FileHash `
        $artifact.FullName `
        $expectedByFile[$artifact.Name] `
        "wheelhouse artifact"
}

$resolvedUv = Resolve-DeploymentFile -Path $UvPath -Label "UvPath"
$resolvedRuntimeRoot = Resolve-DeploymentDirectory `
    -Path $BasePythonRuntimeRootPath `
    -Label "BasePythonRuntimeRootPath"
$resolvedPython = Resolve-DeploymentFile -Path $BasePythonPath -Label "BasePythonPath"
$resolvedRuntimeManifest = Resolve-DeploymentFile `
    -Path $PythonRuntimeManifestPath `
    -Label "PythonRuntimeManifestPath"
$resolvedRuntimeValidator = Resolve-DeploymentFile `
    -Path $PythonRuntimeValidatorPath `
    -Label "PythonRuntimeValidatorPath"
$resolvedReleaseValidator = Resolve-DeploymentFile `
    -Path $ReleaseBundleValidatorPath `
    -Label "ReleaseBundleValidatorPath"
Assert-FileHash $resolvedUv $ExpectedUvSha256 "uv executable"
Assert-FileHash `
    $resolvedRuntimeValidator `
    $ExpectedPythonRuntimeValidatorSha256 `
    "Python runtime validator"
Assert-FileHash `
    $resolvedReleaseValidator `
    $ExpectedReleaseBundleValidatorSha256 `
    "release bundle validator"

try {
    $runtimeValidationOutput = @(& $resolvedRuntimeValidator `
        -RuntimeRootPath $resolvedRuntimeRoot `
        -PythonPath $resolvedPython `
        -ManifestPath $resolvedRuntimeManifest `
        -ExpectedManifestSha256 $ExpectedPythonRuntimeManifestSha256 `
        -ErrorAction Stop)
}
catch {
    throw "Base Python runtime validation failed."
}
if ($runtimeValidationOutput.Count -ne 1 -or
    $runtimeValidationOutput[0] -cne `
        "Python runtime provenance validation passed.") {
    throw "Base Python runtime validation returned an unexpected result."
}

$verificationJson = & $resolvedPython -I -B $resolvedReleaseValidator verify `
    --bundle-root $resolvedBundle `
    --manifest $resolvedManifest `
    --expected-manifest-sha256 $ExpectedReleaseManifestSha256
if ($LASTEXITCODE -ne 0) {
    throw "Release bundle semantic validation failed."
}
try {
    $verified = $verificationJson | ConvertFrom-Json
}
catch {
    throw "Release bundle validator returned invalid output."
}
Assert-ExactObjectProperties $verified @(
    "dependency_lock_file",
    "runtime_inventory_file",
    "mcp_wheel_file",
    "wheelhouse_directory"
) "release bundle validator output"
if ($verified.wheelhouse_directory -cne "wheelhouse") {
    throw "Release bundle validator returned an invalid wheelhouse."
}
foreach ($name in @(
    [string] $verified.dependency_lock_file,
    [string] $verified.runtime_inventory_file,
    [string] $verified.mcp_wheel_file
)) {
    if ([System.IO.Path]::GetFileName($name) -cne $name) {
        throw "Release bundle validator returned an invalid filename."
    }
}
$resolvedLock = Join-Path $resolvedBundle ([string] $verified.dependency_lock_file)
$resolvedInventory = Join-Path $resolvedBundle ([string] $verified.runtime_inventory_file)
$resolvedWheel = Join-Path $resolvedBundle ([string] $verified.mcp_wheel_file)

# VenvPath is intentionally resolved only after every release artifact and the
# full base runtime have passed their independently authenticated preflights.
$resolvedVenv = Resolve-DeploymentDirectory -Path $VenvPath -Label "VenvPath"
if (Get-ChildItem -Force -LiteralPath $resolvedVenv) {
    throw "VenvPath must be empty; use a newly initialized protected root."
}

foreach ($name in @(
    "UV_INDEX",
    "UV_DEFAULT_INDEX",
    "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS",
    "UV_NO_INDEX",
    "UV_OFFLINE",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_FIND_LINKS",
    "PIP_NO_INDEX",
    "PIP_TRUSTED_HOST",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP"
)) {
    [System.Environment]::SetEnvironmentVariable($name, $null, "Process")
}
[System.Environment]::SetEnvironmentVariable("PYTHONNOUSERSITE", "1", "Process")

Invoke-Checked $resolvedUv @(
    "--no-config",
    "venv",
    $resolvedVenv,
    "--python",
    $resolvedPython,
    "--no-python-downloads"
) "Protected virtual environment creation failed."

$venvPython = Join-Path $resolvedVenv "Scripts\python.exe"
Invoke-Checked $resolvedUv @(
    "--no-config",
    "pip",
    "sync",
    "--python",
    $venvPython,
    "--offline",
    "--no-index",
    "--find-links",
    $wheelhousePath,
    "--require-hashes",
    $resolvedLock
) "Hash-locked offline dependency installation failed."

Invoke-Checked $resolvedUv @(
    "--no-config",
    "pip",
    "install",
    "--python",
    $venvPython,
    "--offline",
    "--no-index",
    "--no-deps",
    $resolvedWheel
) "Verified MCP wheel installation failed."

Invoke-Checked $venvPython @(
    "-I",
    "-B",
    "-m",
    "dispenser_conditioning_mcp.deployment_inventory",
    $resolvedInventory
) "Installed Python inventory validation failed."

Write-Output "Authenticated offline Python installation passed."
