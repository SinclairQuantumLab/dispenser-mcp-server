[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $RootPath,

    [Parameter(Mandatory = $true)]
    [string] $ServiceAccount,

    [Parameter(Mandatory = $true)]
    [string] $DeploymentOperatorAccount
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module `
    -Name "$PSHOME\Modules\Microsoft.PowerShell.LocalAccounts\1.0.0.0\Microsoft.PowerShell.LocalAccounts.psd1" `
    -ErrorAction Stop

if (-not [System.IO.Path]::IsPathRooted($RootPath)) {
    throw "RootPath must be absolute."
}
if ([System.IO.Path]::GetFileName($RootPath.TrimEnd("\")) -ne `
    "DispenserConditioningMcp") {
    throw "RootPath must end in DispenserConditioningMcp."
}
if (Test-Path -LiteralPath $RootPath) {
    throw "RootPath must not already exist. Use a fresh deployment root."
}

function Resolve-Sid {
    param([string] $Account)

    try {
        return ([System.Security.Principal.NTAccount] $Account).Translate(
            [System.Security.Principal.SecurityIdentifier]
        )
    }
    catch {
        throw "A required deployment identity could not be resolved."
    }
}

function Assert-EligibleServiceAccount {
    param(
        [System.Security.Principal.SecurityIdentifier] $ServiceSid,
        [System.Security.Principal.SecurityIdentifier] $OperatorSid,
        [System.Security.Principal.SecurityIdentifier] $AdministratorsSid,
        [System.Security.Principal.SecurityIdentifier] $SystemSid
    )

    if ($ServiceSid.Value -eq $OperatorSid.Value -or
        $ServiceSid.Value -eq $AdministratorsSid.Value -or
        $ServiceSid.Value -eq $SystemSid.Value) {
        throw "ServiceAccount must be a distinct non-administrative local user."
    }
    try {
        $localUser = Get-LocalUser -SID $ServiceSid -ErrorAction Stop
        $administratorMembers = @(Get-LocalGroupMember `
            -SID $AdministratorsSid `
            -ErrorAction Stop)
    }
    catch {
        throw "ServiceAccount local-user and Administrators membership checks failed."
    }
    if (-not $localUser.Enabled) {
        throw "ServiceAccount must be an enabled local user."
    }
    foreach ($member in $administratorMembers) {
        if ($null -ne $member.SID -and $member.SID.Value -eq $ServiceSid.Value) {
            throw "ServiceAccount must not be a member of local Administrators."
        }
    }
}

function Assert-EligibleOperatorAccount {
    param(
        [System.Security.Principal.SecurityIdentifier] $OperatorSid,
        [System.Security.Principal.SecurityIdentifier] $AdministratorsSid,
        [System.Security.Principal.SecurityIdentifier] $SystemSid
    )

    if ($OperatorSid.Value -eq $AdministratorsSid.Value -or
        $OperatorSid.Value -eq $SystemSid.Value) {
        throw "DeploymentOperatorAccount must identify a distinct local user."
    }
    try {
        $localUser = Get-LocalUser -SID $OperatorSid -ErrorAction Stop
    }
    catch {
        throw "DeploymentOperatorAccount must identify an enabled local user."
    }
    if (-not $localUser.Enabled) {
        throw "DeploymentOperatorAccount must identify an enabled local user."
    }
}

function Set-ExactDirectoryAcl {
    param(
        [string] $Path,
        [System.Security.Principal.SecurityIdentifier] $ServiceSid,
        [System.Security.Principal.SecurityIdentifier] $OperatorSid,
        [System.Security.Principal.SecurityIdentifier] $AdministratorsSid,
        [System.Security.Principal.SecurityIdentifier] $SystemSid,
        [System.Security.AccessControl.FileSystemRights] $ServiceRights,
        [System.Security.AccessControl.InheritanceFlags] $ServiceInheritance
    )

    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($OperatorSid)
    $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $propagation = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    foreach ($sid in @($OperatorSid, $AdministratorsSid, $SystemSid)) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            $propagation,
            $allow
        )))
    }
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $ServiceSid,
        $ServiceRights,
        $ServiceInheritance,
        $propagation,
        $allow
    )))
    [System.IO.Directory]::SetAccessControl($Path, $acl)
}

# Resolve and validate identities before the first filesystem mutation.
$serviceSid = Resolve-Sid $ServiceAccount
$operatorSid = Resolve-Sid $DeploymentOperatorAccount
$administratorsSid = New-Object System.Security.Principal.SecurityIdentifier(
    "S-1-5-32-544"
)
$systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
Assert-EligibleOperatorAccount $operatorSid $administratorsSid $systemSid
Assert-EligibleServiceAccount `
    $serviceSid `
    $operatorSid `
    $administratorsSid `
    $systemSid

$allChildren = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
    [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
$noChildren = [System.Security.AccessControl.InheritanceFlags]::None
$relativeDirectories = @(
    "app",
    "dependencies",
    "dependencies\hicube",
    "dependencies\py-siglent-spd3000",
    "venv",
    "config",
    "config\unloaded-hil",
    "config\production",
    "auth",
    "auth\unloaded-hil",
    "auth\production",
    "state",
    "state\unloaded-hil",
    "logs"
)

# Root creation and DACL assignment are separate Windows operations. The fresh,
# operator-controlled parent is therefore required. No descendant is created
# during that brief interval; the root is protected before descendant creation.
[void](New-Item -ItemType Directory -Path $RootPath)
Set-ExactDirectoryAcl `
    $RootPath `
    $serviceSid `
    $operatorSid `
    $administratorsSid `
    $systemSid `
    ([System.Security.AccessControl.FileSystemRights]::ReadAndExecute) `
    $noChildren
foreach ($relative in $relativeDirectories) {
    $path = Join-Path $RootPath $relative
    [void](New-Item -ItemType Directory -Path $path)
    $rights = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
    if ($relative -eq "logs" -or $relative -eq "state\unloaded-hil") {
        $rights = [System.Security.AccessControl.FileSystemRights]::Modify
    }
    Set-ExactDirectoryAcl `
        $path `
        $serviceSid `
        $operatorSid `
        $administratorsSid `
        $systemSid `
        $rights `
        $allChildren
}

Write-Output "Protected deployment root initialization passed."
