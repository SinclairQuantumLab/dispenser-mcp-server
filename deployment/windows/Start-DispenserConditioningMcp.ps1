# Archived 0.5.1 environment-profile launcher. Package 0.6.1 uses fixed TOML
# settings from its source checkout; do not use this launcher with 0.6.1.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ProfilePath,

    [Parameter(Mandatory = $true)]
    [string] $PythonPath,

    [switch] $ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module -Name Microsoft.PowerShell.Utility -ErrorAction Stop

$allowedVariables = @(
    "DISPENSER_MCP_TRANSPORT",
    "DISPENSER_MCP_HTTP_BIND_HOST",
    "DISPENSER_MCP_HTTP_PORT",
    "DISPENSER_MCP_HTTP_PATH",
    "DISPENSER_MCP_HTTP_TRUST_MODE",
    "DISPENSER_MCP_HTTP_ALLOWED_HOSTS",
    "DISPENSER_MCP_HTTP_ALLOWED_ORIGINS",
    "DISPENSER_HICUBE_CLIENT_FILE",
    "DISPENSER_HICUBE_HOST",
    "DISPENSER_HICUBE_PORT",
    "DISPENSER_HICUBE_TIMEOUT_S",
    "DISPENSER_SIGLENT_DRIVER_SRC",
    "DISPENSER_SIGLENT_CONNECTION",
    "DISPENSER_SIGLENT_IDENTIFIER",
    "DISPENSER_SIGLENT_GATEWAY_AUTH_FILE",
    "DISPENSER_SIGLENT_TIMEOUT_S",
    "DISPENSER_SIGLENT_MIN_COMMAND_INTERVAL_MS",
    "DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT",
    "DISPENSER_SIGLENT_UNLOADED_HIL_STATE_FILE",
    "DISPENSER_SIGLENT_UNLOADED_HIL_TRIP_LATCH_FILE",
    "DISPENSER_SIGLENT_TOPOLOGY",
    "DISPENSER_SIGLENT_CHANNEL",
    "DISPENSER_SIGLENT_EXPECTED_MODEL",
    "DISPENSER_SIGLENT_EXPECTED_SERIAL_NUMBER",
    "DISPENSER_SIGLENT_COMPLIANCE_VOLTAGE_V",
    "DISPENSER_SIGLENT_MAX_LOAD_CURRENT_A",
    "DISPENSER_SIGLENT_UPWARD_STEP_A",
    "DISPENSER_SIGLENT_CONTROL_ENABLED"
)

if (-not [System.IO.Path]::IsPathRooted($ProfilePath)) {
    throw "ProfilePath must be absolute."
}
if (-not [System.IO.Path]::IsPathRooted($PythonPath)) {
    throw "PythonPath must be absolute."
}

$resolvedProfile = (Resolve-Path -LiteralPath $ProfilePath).Path
$resolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path
if ((Get-Item -LiteralPath $resolvedProfile).PSIsContainer) {
    throw "ProfilePath must identify a PowerShell data file."
}
if ((Get-Item -LiteralPath $resolvedPython).PSIsContainer -or
    [System.IO.Path]::GetExtension($resolvedPython) -ne ".exe") {
    throw "PythonPath must identify a Python executable."
}

$profileData = Import-PowerShellDataFile -LiteralPath $resolvedProfile
if ($profileData -isnot [hashtable]) {
    throw "The deployment profile must contain one PowerShell hashtable."
}

foreach ($key in $profileData.Keys) {
    if ($key -isnot [string] -or $allowedVariables -notcontains $key) {
        throw "The deployment profile contains an unsupported setting."
    }
    $value = $profileData[$key]
    if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
        throw "Every deployment profile value must be a non-empty string."
    }
}

foreach ($name in $allowedVariables) {
    [System.Environment]::SetEnvironmentVariable($name, $null, "Process")
}
foreach ($key in $profileData.Keys) {
    [System.Environment]::SetEnvironmentVariable(
        [string] $key,
        [string] $profileData[$key],
        "Process"
    )
}

if (-not $profileData.ContainsKey("DISPENSER_MCP_TRANSPORT") -or
    -not $profileData.ContainsKey("DISPENSER_SIGLENT_CONTROL_ENABLED") -or
    -not $profileData.ContainsKey("DISPENSER_SIGLENT_ACCEPTANCE_CONTEXT")) {
    throw "The profile must explicitly bind transport, control, and acceptance context."
}

[System.Environment]::SetEnvironmentVariable("PYTHONPATH", $null, "Process")
[System.Environment]::SetEnvironmentVariable("PYTHONHOME", $null, "Process")
[System.Environment]::SetEnvironmentVariable("PYTHONINSPECT", $null, "Process")
[System.Environment]::SetEnvironmentVariable("PYTHONSTARTUP", $null, "Process")
[System.Environment]::SetEnvironmentVariable("PYTHONNOUSERSITE", "1", "Process")

if ($ValidateOnly) {
    & $resolvedPython -I -m dispenser_conditioning_mcp.deployment_check
}
else {
    & $resolvedPython -I -m dispenser_conditioning_mcp
}
exit $LASTEXITCODE
