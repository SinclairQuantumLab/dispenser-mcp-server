[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $RootPath,

    [Parameter(Mandatory = $true)]
    [ValidateSet("confirmed_outputs_off_and_no_unapproved_load")]
    [string] $PhysicalVerification
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$principal = [System.Security.Principal.WindowsPrincipal]::new(
    [System.Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole(
    [System.Security.Principal.WindowsBuiltInRole]::Administrator
)) {
    throw "Unloaded-HIL state initialization requires an elevated operator."
}

$resolvedRoot = (Resolve-Path -LiteralPath $RootPath -ErrorAction Stop).Path.TrimEnd("\")
$stateDirectory = Join-Path $resolvedRoot "state\unloaded-hil"
$directoryItem = Get-Item -LiteralPath $stateDirectory -Force -ErrorAction Stop
if (-not $directoryItem.PSIsContainer -or
    ($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "The unloaded-HIL state directory is not an approved real directory."
}

$statePath = Join-Path $stateDirectory "operation-state.json"
if (Test-Path -LiteralPath $statePath) {
    throw "Durable state already exists and is never overwritten."
}

$record = [ordered]@{
    record_type = "initialized_state"
    schema_version = 1
    initialized_at = [DateTimeOffset]::UtcNow.ToString("o")
}
$bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
    (($record | ConvertTo-Json -Depth 3) + "`n")
)

$stream = $null
try {
    $stream = [System.IO.FileStream]::new(
        $statePath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None,
        4096,
        [System.IO.FileOptions]::WriteThrough
    )
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush($true)
}
finally {
    if ($null -ne $stream) {
        $stream.Dispose()
    }
}

$verified = [System.IO.File]::ReadAllBytes($statePath)
if (-not [System.Linq.Enumerable]::SequenceEqual[byte]($bytes, $verified)) {
    throw "Initialized durable state could not be verified."
}

Write-Output "Unloaded-HIL state initialization passed."
