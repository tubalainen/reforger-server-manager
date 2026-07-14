<#
.SYNOPSIS
    Removes the Reforger Server Manager from Windows.

.DESCRIPTION
    Run it from the install folder:

        cd $env:USERPROFILE\ReforgerServerManager
        powershell -ExecutionPolicy Bypass -File .\uninstall.ps1

    ...or, if the install is broken or the folder is gone, download it and run it
    from anywhere:

        $u = "$env:TEMP\reforger-uninstall.ps1"
        Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/uninstall.ps1 -OutFile $u
        powershell -ExecutionPolicy Bypass -File $u

    BY DEFAULT YOUR SERVERS' DATA IS KEPT. The Docker volumes holding your
    templates, instances, saved games and the ~10 GB of downloaded server files are
    left alone, so you can reinstall and pick up where you left off. Pass -RemoveData
    to delete those too — that is not reversible.

    Docker Desktop and WSL2 are never touched: other things on your machine may be
    using them. Remove them from "Add or remove programs" if you want them gone.

.PARAMETER RemoveData
    Also delete the Docker volumes: templates, instances, SAVED GAMES and the
    downloaded server files. Cannot be undone.

.PARAMETER RemoveImages
    Also delete the Docker images (manager, Arma server, steamcmd). Frees several
    GB; they are re-downloaded on the next install.

.PARAMETER Yes
    Do not ask for confirmation.
#>
[CmdletBinding()]
param(
    [string] $InstallDir = (Join-Path $env:USERPROFILE 'ReforgerServerManager'),
    [switch] $RemoveData,
    [switch] $RemoveImages,
    [switch] $Yes
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# The helpers sit next to this script in the install folder; when it is run from
# %TEMP% on a broken install they are not there, so define the two we need inline.
$commonPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'common.ps1'
if (Test-Path $commonPath) {
    . $commonPath
} else {
    function Write-Step  { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
    function Write-Ok    { param($m) Write-Host "    [ok] $m" -ForegroundColor Green }
    function Write-Warn2 { param($m) Write-Host "    [!]  $m" -ForegroundColor Yellow }
    function Write-Info  { param($m) Write-Host "    $m" -ForegroundColor Gray }
    function Test-Admin {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    function Invoke-Quiet {
        param([Parameter(Mandatory)][string] $CommandLine)
        & cmd.exe /c "$CommandLine >nul 2>&1"
        return ($LASTEXITCODE -eq 0)
    }
    function Get-DockerCli {
        param([switch] $Quiet)
        $cmd = Get-Command docker -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        $fallback = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
        if (Test-Path $fallback) { return $fallback }
        if ($Quiet) { return $null }
        throw 'Docker Desktop is not installed.'
    }
    function Test-DockerEngine {
        param([string] $Cli)
        if (-not $Cli) { return $false }
        return (Invoke-Quiet "`"$Cli`" info")
    }
}

$RuleName   = 'Arma Reforger (game + A2S)'
$Shortcut   = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Reforger Server Manager.lnk'
$ResumeDir  = Join-Path $env:LOCALAPPDATA 'ReforgerServerManager'
$RunOnceKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
$Volumes    = @('reforger-data', 'reforger-serverfiles-stable', 'reforger-serverfiles-experimental')

Write-Host ''
Write-Host '  Reforger Server Manager - uninstall' -ForegroundColor White

# --- What is actually here? --------------------------------------------------
Write-Step 'Looking for what is installed'

$docker = Get-DockerCli -Quiet
$engineUp = Test-DockerEngine -Cli $docker
$found = @()

if (Test-Path $InstallDir) { $found += "install folder        $InstallDir" }
if (Test-Path $Shortcut)   { $found += "Desktop shortcut      $Shortcut" }
if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    $found += "firewall rule         $RuleName"
}
if (Test-Path $ResumeDir) { $found += "installer resume dir  $ResumeDir" }
if (Get-ItemProperty -Path $RunOnceKey -Name 'ReforgerServerManagerInstall' -ErrorAction SilentlyContinue) {
    $found += "installer RunOnce entry"
}

$containers = @()
$volumesPresent = @()
if ($engineUp) {
    $containers = @(& $docker ps -a -q --filter 'label=reforger-manager.managed=true')
    if ($containers.Count) { $found += "Docker containers     $($containers.Count) (manager + servers)" }
    foreach ($v in $Volumes) {
        if (Invoke-Quiet "`"$docker`" volume inspect $v") { $volumesPresent += $v }
    }
    if ($volumesPresent.Count) {
        $found += "Docker volumes        $($volumesPresent -join ', ')"
    }
} else {
    Write-Warn2 'The Docker engine is not running, so Docker containers/volumes cannot be touched.'
    Write-Info  'Start Docker Desktop and re-run this if you want those removed too.'
}

if (-not $found.Count) {
    Write-Host ''
    Write-Ok 'Nothing to remove - this machine looks clean already.'
    Write-Host ''
    return
}

# --- Say exactly what will happen, then ask ----------------------------------
Write-Host ''
Write-Host '  Found:' -ForegroundColor White
foreach ($f in $found) { Write-Host "    - $f" }

Write-Host ''
Write-Host '  Will be REMOVED:' -ForegroundColor White
Write-Host '    - the manager and any Arma server containers'
Write-Host '    - the install folder (including .env, which holds your admin password)'
Write-Host '    - the Desktop shortcut and the firewall rule'
if ($RemoveImages) { Write-Host '    - the Docker images (manager, Arma server, steamcmd)' }
if ($RemoveData) {
    Write-Host ''
    Write-Host '    - THE DOCKER VOLUMES: your templates, instances, SAVED GAMES and the' -ForegroundColor Red
    Write-Host '      ~10 GB of downloaded server files. This cannot be undone.' -ForegroundColor Red
}

Write-Host ''
Write-Host '  Will be KEPT:' -ForegroundColor White
if (-not $RemoveData) {
    Write-Host '    - your data: templates, instances, saved games, downloaded server files'
    Write-Host '      (the Docker volumes. Re-installing picks them up again.'
    Write-Host '       Pass -RemoveData to delete them.)'
}
Write-Host '    - Docker Desktop and WSL2 (other things may use them; remove them from'
Write-Host '      "Add or remove programs" if you want them gone)'
Write-Host ''

if (-not $Yes) {
    $answer = Read-Host '  Type REMOVE to continue'
    if ($answer -cne 'REMOVE') {
        Write-Host ''
        Write-Info 'Nothing was changed.'
        return
    }
}

# --- Containers, network, volumes, images ------------------------------------
if ($engineUp) {
    Write-Step 'Removing containers'
    $compose = Join-Path $InstallDir 'docker-compose.windows.yaml'
    if (Test-Path $compose) {
        & $docker compose -f $compose down --remove-orphans 2>&1 | Out-Null
    }
    # Anything the manager created carries our label, whether compose knows it or not.
    $containers = @(& $docker ps -a -q --filter 'label=reforger-manager.managed=true')
    foreach ($id in $containers) { $null = Invoke-Quiet "`"$docker`" rm -f $id" }
    if (Invoke-Quiet "`"$docker`" container inspect reforger-manager") {
        $null = Invoke-Quiet "`"$docker`" rm -f reforger-manager"
    }
    Write-Ok "removed $($containers.Count) container(s)"

    Write-Step 'Removing the Docker network'
    if (Invoke-Quiet "`"$docker`" network inspect reforger-net") {
        $null = Invoke-Quiet "`"$docker`" network rm reforger-net"
        Write-Ok 'reforger-net'
    } else {
        Write-Info 'no reforger-net network'
    }

    if ($RemoveData -and $volumesPresent.Count) {
        Write-Step 'Deleting the data volumes (this is the irreversible bit)'
        foreach ($v in $volumesPresent) {
            if (Invoke-Quiet "`"$docker`" volume rm $v") { Write-Ok "deleted $v" }
            else { Write-Warn2 "could not delete $v (is something still using it?)" }
        }
    } elseif ($volumesPresent.Count) {
        Write-Step 'Keeping your data'
        Write-Info "kept: $($volumesPresent -join ', ')"
        Write-Info 'Re-installing will find them again. Use -RemoveData to delete them.'
    }

    if ($RemoveImages) {
        Write-Step 'Removing the Docker images'
        foreach ($img in @('ghcr.io/tubalainen/reforger-server-manager',
                           'ghcr.io/acemod/arma-reforger',
                           'steamcmd/steamcmd')) {
            if (Invoke-Quiet "`"$docker`" image rm -f $img") { Write-Ok $img }
        }
    }
}

# --- Firewall rule (needs admin) ---------------------------------------------
Write-Step 'Removing the firewall rule'
if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    if (Test-Admin) {
        Remove-NetFirewallRule -DisplayName $RuleName
        Write-Ok $RuleName
    } else {
        Write-Info 'Asking for administrator rights...'
        $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @(
            '-NoProfile', '-Command',
            "Remove-NetFirewallRule -DisplayName '$RuleName'")
        if ($p.ExitCode -eq 0) {
            Write-Ok $RuleName
        } else {
            Write-Warn2 'Could not remove it. Run this in an ELEVATED PowerShell:'
            Write-Host  "    Remove-NetFirewallRule -DisplayName '$RuleName'" -ForegroundColor Yellow
        }
    }
} else {
    Write-Info 'no firewall rule'
}

# --- Shortcut, resume state, install folder ----------------------------------
Write-Step 'Removing the shortcut and installer state'
if (Test-Path $Shortcut) { Remove-Item $Shortcut -Force; Write-Ok 'Desktop shortcut' }
Remove-ItemProperty -Path $RunOnceKey -Name 'ReforgerServerManagerInstall' -ErrorAction SilentlyContinue
if (Test-Path $ResumeDir) { Remove-Item $ResumeDir -Recurse -Force; Write-Ok 'installer resume files' }

Write-Step 'Removing the install folder'
if (Test-Path $InstallDir) {
    # Do not delete the folder we are running from out from under ourselves.
    $self = Split-Path -Parent $MyInvocation.MyCommand.Path
    if ($self -eq (Resolve-Path $InstallDir).Path) {
        $stage = Join-Path $env:TEMP 'reforger-uninstall-finish.ps1'
        Set-Content -Path $stage -Encoding ASCII -Value @"
Start-Sleep -Seconds 2
Remove-Item -LiteralPath '$InstallDir' -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ''
Write-Host '  Removed $InstallDir' -ForegroundColor Green
Write-Host '  Uninstall complete.' -ForegroundColor Green
Write-Host ''
Read-Host '  Press Enter to close'
"@
        Write-Info 'The install folder is where this script lives, so a helper finishes the job.'
        Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$stage`"")
        return
    }
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    Write-Ok $InstallDir
} else {
    Write-Info 'no install folder'
}

Write-Host ''
Write-Host '  Uninstall complete.' -ForegroundColor Green
if (-not $RemoveData) {
    Write-Host '  Your templates, instances and saved games are still in the Docker volumes.' -ForegroundColor Gray
    Write-Host '  Re-install and they come back. Run with -RemoveData to wipe them for good.' -ForegroundColor Gray
}
Write-Host ''
