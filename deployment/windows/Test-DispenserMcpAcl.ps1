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

function Resolve-SidValue {
    param([string] $Account)

    try {
        return ([System.Security.Principal.NTAccount] $Account).Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw "A required deployment identity could not be resolved."
    }
}

function Assert-EligibleServiceAccount {
    param(
        [string] $ServiceSid,
        [string] $OperatorSid,
        [string] $AdministratorsSid,
        [string] $SystemSid
    )

    if ($ServiceSid -eq $OperatorSid -or
        $ServiceSid -eq $AdministratorsSid -or
        $ServiceSid -eq $SystemSid) {
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
        if ($null -ne $member.SID -and $member.SID.Value -eq $ServiceSid) {
            throw "ServiceAccount must not be a member of local Administrators."
        }
    }
}

function Assert-EligibleOperatorAccount {
    param(
        [string] $OperatorSid,
        [string] $AdministratorsSid,
        [string] $SystemSid
    )

    if ($OperatorSid -eq $AdministratorsSid -or $OperatorSid -eq $SystemSid) {
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

if (-not [System.IO.Path]::IsPathRooted($RootPath)) {
    throw "RootPath must be absolute."
}
$resolvedRoot = (Resolve-Path -LiteralPath $RootPath).Path.TrimEnd("\")
if ([System.IO.Path]::GetFileName($resolvedRoot) -ne "DispenserConditioningMcp") {
    throw "RootPath must end in DispenserConditioningMcp."
}
if (-not (Get-Item -Force -LiteralPath $resolvedRoot).PSIsContainer) {
    throw "RootPath must identify a directory."
}

$serviceSid = Resolve-SidValue $ServiceAccount
$operatorSid = Resolve-SidValue $DeploymentOperatorAccount
$administratorsSid = "S-1-5-32-544"
$systemSid = "S-1-5-18"
Assert-EligibleOperatorAccount $operatorSid $administratorsSid $systemSid
Assert-EligibleServiceAccount `
    $serviceSid `
    $operatorSid `
    $administratorsSid `
    $systemSid
$allowedSids = @($serviceSid, $operatorSid, $administratorsSid, $systemSid)
$protectedDirectories = @(
    "",
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
foreach ($relative in $protectedDirectories) {
    $requiredPath = if ($relative) {
        Join-Path $resolvedRoot $relative
    }
    else {
        $resolvedRoot
    }
    if (-not [System.IO.Directory]::Exists($requiredPath)) {
        throw "Deployment tree is missing a required policy directory."
    }
}
$allItems = @(
    Get-Item -Force -LiteralPath $resolvedRoot
    Get-ChildItem -Force -Recurse -LiteralPath $resolvedRoot
)

foreach ($item in $allItems) {
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Deployment tree contains a forbidden reparse point."
    }
    $acl = if ($item.PSIsContainer) {
        [System.IO.Directory]::GetAccessControl($item.FullName)
    }
    else {
        [System.IO.File]::GetAccessControl($item.FullName)
    }
    $relative = $item.FullName.Substring($resolvedRoot.Length).TrimStart("\")
    $isPolicyDirectory = $item.PSIsContainer -and `
        $protectedDirectories -contains $relative
    if ($isPolicyDirectory -and
        -not $acl.AreAccessRulesProtected) {
        throw "A deployment policy directory has unprotected ACL inheritance."
    }
    if (-not $acl.AreAccessRulesCanonical) {
        throw "Deployment ACL is not canonical."
    }
    $ownerSid = $acl.GetOwner(
        [System.Security.Principal.SecurityIdentifier]
    ).Value
    $isWritableServicePath = $relative -eq "logs" -or `
        $relative.StartsWith("logs\", [System.StringComparison]::OrdinalIgnoreCase) -or `
        $relative -eq "state\unloaded-hil" -or `
        $relative.StartsWith(
            "state\unloaded-hil\",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    $allowedOwners = if ($isWritableServicePath) {
        @($operatorSid, $serviceSid)
    }
    else {
        @($operatorSid)
    }
    if ($allowedOwners -notcontains $ownerSid) {
        throw "Deployment item owner is not approved for this path."
    }
    $synchronize = [System.Security.AccessControl.FileSystemRights]::Synchronize
    $serviceMaximum = if ($isWritableServicePath) {
        [System.Security.AccessControl.FileSystemRights]::Modify -bor $synchronize
    }
    else {
        [System.Security.AccessControl.FileSystemRights]::ReadAndExecute -bor $synchronize
    }
    $allChildren = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $noChildren = [System.Security.AccessControl.InheritanceFlags]::None
    $noPropagation = [System.Security.AccessControl.PropagationFlags]::None
    $seen = @{}
    foreach ($sid in $allowedSids) {
        $seen[$sid] = 0
    }

    foreach ($rule in $acl.Access) {
        try {
            $sid = $rule.IdentityReference.Translate(
                [System.Security.Principal.SecurityIdentifier]
            ).Value
        }
        catch {
            throw "Deployment ACL contains an unresolvable identity."
        }
        if ($allowedSids -notcontains $sid) {
            throw "Deployment ACL contains an unapproved identity."
        }
        if ($rule.AccessControlType -ne `
            [System.Security.AccessControl.AccessControlType]::Allow) {
            throw "Deployment ACL contains a deny entry."
        }
        $expectedInherited = -not $isPolicyDirectory
        if ($rule.IsInherited -ne $expectedInherited) {
            throw "Deployment ACL inheritance origin is not exact."
        }
        $expectedInheritance = if ($isPolicyDirectory) {
            if ($sid -eq $serviceSid -and $relative -eq "") {
                $noChildren
            }
            else {
                $allChildren
            }
        }
        elseif ($item.PSIsContainer) {
            $allChildren
        }
        else {
            $noChildren
        }
        if ([int] $rule.InheritanceFlags -ne [int] $expectedInheritance -or
            [int] $rule.PropagationFlags -ne [int] $noPropagation) {
            throw "Deployment ACL inheritance and propagation flags are not exact."
        }
        $seen[$sid] += 1
        if ($seen[$sid] -ne 1) {
            throw "Deployment ACL contains duplicate identity rules."
        }
        if ($sid -eq $serviceSid) {
            if ([int64] $rule.FileSystemRights -ne [int64] $serviceMaximum) {
                throw "Service account deployment rights are not exact."
            }
        }
        else {
            if ([int64] $rule.FileSystemRights -ne [int64] `
                [System.Security.AccessControl.FileSystemRights]::FullControl) {
                throw "Privileged deployment rights are not exact."
            }
        }
    }
    foreach ($sid in $allowedSids) {
        if ($seen[$sid] -ne 1) {
            throw "Deployment ACL is missing a required identity."
        }
    }
}

Write-Output "Recursive deployment ACL and owner validation passed."
