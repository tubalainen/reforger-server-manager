<#
.SYNOPSIS
    Starts, stops or removes the Reforger Server Supervisor on Windows.

.DESCRIPTION
    The Supervisor is one read-only page showing every Reforger Server Manager
    install on this PC - each team's stack, its servers, players, CPU and memory,
    and anything that needs attention (#204). It changes nothing: every control
    stays in each team's own manager.

    It is installed by install.ps1 -Supervisor into its own folder
    (ReforgerSupervisor in your user folder), and the "Reforger Server
    Supervisor" Desktop shortcut runs this script:

        supervisor.ps1                  start it and open the page (the default)
        supervisor.ps1 -Action stop     stop it
        supervisor.ps1 -Action uninstall [-RemoveData]
                                        remove it; -RemoveData also deletes its
                                        login state (nothing else is stored)

    Starting refreshes this script from GitHub and pulls the image, like the
    manager's start.ps1; -NoUpdate skips both.
#>
[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'uninstall')]
    [string] $Action = 'start',

    # start: skip the script refresh and the image pull.
    [switch] $NoUpdate,
    # start: do not open the browser.
    [switch] $NoBrowser,
    # start: set on the relaunch after a script update, to prevent a loop.
    [switch] $NoSelfUpdate,
    [string] $ScriptsRef = 'main',
    [int] $DockerTimeout = 300,

    # uninstall: also delete the Supervisor's Docker volumes.
    [switch] $RemoveData,
    # uninstall: do not ask.
    [switch] $Yes
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
. (Join-Path $here 'common.ps1')

$Compose = Join-Path $here 'docker-compose.supervisor.yaml'
$EnvFile = Join-Path $here '.env'
$Shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Reforger Server Supervisor.lnk'

function Get-EnvValue {
    param([string] $Key, [string] $Default)
    if (-not (Test-Path $EnvFile)) { return $Default }
    $line = Select-String -Path $EnvFile -Pattern "^$Key=(.*)$" -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($line -and $line.Matches[0].Groups[1].Value.Trim()) {
        return $line.Matches[0].Groups[1].Value.Trim()
    }
    return $Default
}

function Stop-WithMessage {
    param([string] $Message)
    Write-Host ''
    Write-Warn2 $Message
    Write-Host ''
    Read-Host '  Press Enter to close this window'
    exit 1
}

Write-Host ''
Write-Host '  Reforger Server Supervisor' -ForegroundColor White

if (-not (Test-Path $Compose)) {
    Stop-WithMessage "docker-compose.supervisor.yaml is missing from $here - run install.ps1 -Supervisor again."
}
$docker = Get-DockerCli -Quiet
if (-not $docker) { Stop-WithMessage 'Docker Desktop is not installed. Run the installer from the README first.' }
$composeArgs = @('compose', '-f', $Compose, '--env-file', $EnvFile)

switch ($Action) {
    'start' {
        if (-not $NoUpdate -and -not $NoSelfUpdate) {
            Write-Step 'Checking for script updates'
            $changed = Update-ManagerScripts -InstallDir $here -Ref $ScriptsRef -Names @('supervisor.ps1', 'common.ps1')
            if ($null -eq $changed) {
                Write-Info 'Could not check for updates (offline?) - using the scripts already on disk.'
            } elseif ($changed -contains 'supervisor.ps1') {
                Write-Info 'Relaunching with the updated script...'
                & (Join-Path $here 'supervisor.ps1') @PSBoundParameters -NoSelfUpdate
                exit $LASTEXITCODE
            } elseif ($changed.Count -gt 0) {
                Write-Ok ("Updated: {0}" -f ($changed -join ', '))
            }
        }

        Write-Step 'Starting Docker'
        if (-not (Test-WslInstalled)) {
            Stop-WithMessage 'WSL2 is not installed, so Docker Desktop cannot start. Run install.ps1 again.'
        }
        if (-not (Wait-DockerEngine -Cli $docker -TimeoutSeconds $DockerTimeout)) {
            Stop-WithMessage 'Docker is not running, so the Supervisor cannot be started yet.'
        }

        if (-not $NoUpdate) {
            Write-Step 'Updating the image'
            & $docker @composeArgs pull
            if ($LASTEXITCODE -ne 0) { Write-Warn2 'Could not pull the image (offline?) - starting the one on disk.' }
        }
        Write-Step 'Starting the Supervisor'
        & $docker @composeArgs up -d
        if ($LASTEXITCODE -ne 0) { Stop-WithMessage 'docker compose up failed - see the output above.' }

        $port = Get-EnvValue -Key 'WEB_PORT' -Default '7090'
        $url = "http://localhost:$port"
        Write-Step 'Waiting for it to answer'
        if (Wait-ManagerHealth -Url "http://127.0.0.1:$port/api/health" -TimeoutSeconds 60) {
            Write-Host ''
            Write-Ok "The Supervisor is up at $url"
        } else {
            Write-Host ''
            Write-Warn2 "It did not answer on $url yet - give it a moment, then refresh the page."
        }
        if (-not $NoBrowser) { Start-Process $url }
        Write-Host ''
        Write-Host "  Supervisor : $url   (user: admin, password: see $EnvFile)"
        Write-Host ''
        Start-Sleep -Seconds 4
    }

    'stop' {
        Write-Step 'Stopping the Supervisor'
        & $docker @composeArgs stop
        if ($LASTEXITCODE -ne 0) { throw 'docker compose stop failed.' }
        Write-Ok 'Stopped. The stacks and their game servers are not affected.'
    }

    'uninstall' {
        Write-Host ''
        Write-Host '  Will be REMOVED:' -ForegroundColor White
        Write-Host '    - the Supervisor and its observer (two containers)'
        Write-Host "    - the Desktop shortcut and $here (including .env)"
        if ($RemoveData) { Write-Host '    - its login state (a password changed on its page)' }
        Write-Host '  Not touched: every stack, its servers and its data.' -ForegroundColor Gray
        if (-not $Yes) {
            $answer = Read-Host '  Type REMOVE to continue'
            if ($answer -cne 'REMOVE') { Write-Info 'Cancelled.'; return }
        }
        Write-Step 'Removing the containers'
        $downArgs = @('down', '--remove-orphans')
        if ($RemoveData) { $downArgs += '--volumes' }
        & $docker @composeArgs @downArgs
        if ($LASTEXITCODE -ne 0) { Write-Warn2 'docker compose down reported a problem - see above.' }
        if (Test-Path $Shortcut) { Remove-Item $Shortcut -Force; Write-Ok 'Desktop shortcut' }

        # This script lives in the folder: a helper deletes it once we have exited.
        $stage = Join-Path $env:TEMP 'reforger-supervisor-remove.ps1'
        Set-Content -Path $stage -Encoding ASCII -Value @"
Start-Sleep -Seconds 2
Remove-Item -LiteralPath '$here' -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ''
Write-Host '  Removed $here - the Supervisor is uninstalled.' -ForegroundColor Green
Write-Host ''
Read-Host '  Press Enter to close'
"@
        Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$stage`"")
    }
}
