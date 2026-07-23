<#
.SYNOPSIS
    Starts Docker Desktop (and with it WSL2), brings the Reforger Server Manager
    up, and opens the web GUI. This is what the Desktop shortcut runs.

.DESCRIPTION
    Safe to run at any time: if everything is already running it just opens the GUI.

    By default it pulls the manager image before starting, so a double-click of the
    Desktop shortcut keeps you on the newest release. The tag it pulls comes from
    MANAGER_VERSION in .env ('latest' by default); pin it to a release such as
    v0.31.0 to LOCK the version, and this pull will then just confirm that one is
    present instead of moving you forward. Pass -NoUpdate to skip the pull entirely
    (e.g. offline, or to start as fast as possible).
#>
[CmdletBinding()]
param(
    # Skip pulling the manager image; start whatever is already on disk.
    [switch] $NoUpdate,

    # Accepted for backwards compatibility — pulling is now the default.
    [switch] $Update,

    # Do not open the browser at the end.
    [switch] $NoBrowser,

    # Seconds to wait for the Docker engine to come up.
    [int] $DockerTimeout = 300
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
. (Join-Path $here 'common.ps1')

$Compose = Join-Path $here 'docker-compose.windows.yaml'
$EnvFile = Join-Path $here '.env'

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

function Pause-OnExit {
    param([string] $Message)
    Write-Host ''
    Write-Warn2 $Message
    Write-Host ''
    Read-Host '  Press Enter to close this window'
    exit 1
}

Write-Host ''
Write-Host '  Reforger Server Manager' -ForegroundColor White

if (-not (Test-Path $Compose)) {
    Pause-OnExit "docker-compose.windows.yaml is missing from $here - re-run the installer."
}

$docker = Get-DockerCli -Quiet
if (-not $docker) {
    Pause-OnExit 'Docker Desktop is not installed. Run the installer from the README first.'
}

$webPort = Get-EnvValue -Key 'WEB_PORT' -Default '7780'
$url = "http://localhost:$webPort"

# --- 1. Docker engine (starting Docker Desktop also starts the WSL2 VM) -----
# Wait-DockerEngine never throws on a down daemon and tells the user to click Skip
# on Docker Desktop's sign-in screen if that is what it is waiting for (#51).
Write-Step 'Starting Docker'
if (-not (Test-WslInstalled)) {
    Pause-OnExit ('WSL2 is not installed, so Docker Desktop cannot start. Re-run the ' +
                  'installer - it will install WSL2 and reboot for you.')
}
if (-not (Wait-DockerEngine -Cli $docker -TimeoutSeconds $DockerTimeout)) {
    Pause-OnExit 'Docker is not running, so the manager cannot be started yet.'
}

# --- 2. Firewall sanity check (the installer creates this rule) -------------
$ruleName = 'Arma Reforger (game + A2S)'
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    Write-Warn2 'The firewall rule for the game ports is missing - players will not be able to join.'
    Write-Host  '    Run this once in an ELEVATED PowerShell:' -ForegroundColor Yellow
    Write-Host  "    powershell -ExecutionPolicy Bypass -File `"$(Join-Path $here 'firewall.ps1')`"" -ForegroundColor Yellow
}

# --- 3. The manager itself ---------------------------------------------------
# Which manager build .env asks for. 'latest' follows every release; a pinned
# tag (e.g. v0.31.0) locks it. --env-file makes the ${MANAGER_VERSION} in the
# compose file resolve to it for both the pull and the up.
$managerVersion = Get-EnvValue -Key 'MANAGER_VERSION' -Default 'latest'
$composeArgs = @('compose', '-f', $Compose, '--env-file', $EnvFile)

if ($NoUpdate) {
    Write-Info "Skipping the image pull (-NoUpdate); running manager version '$managerVersion'."
} elseif ($managerVersion -eq 'latest') {
    Write-Step 'Updating the manager image (following latest)'
    & $docker @composeArgs pull
    if ($LASTEXITCODE -ne 0) {
        # A pull failure (usually just offline) must not stop a server that is
        # already installed from starting — carry on with the image on disk.
        Write-Warn2 'Could not pull the manager image (offline?) - starting the version already on disk.'
    }
} else {
    Write-Step "Making sure the locked manager image ($managerVersion) is present"
    & $docker @composeArgs pull
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "Could not pull $managerVersion (offline?) - starting the version already on disk."
    }
}

Write-Step 'Starting the manager'
& $docker @composeArgs up -d
if ($LASTEXITCODE -ne 0) { Pause-OnExit 'docker compose up failed - see the output above.' }

# --- 4. Wait for the API, then open the GUI ---------------------------------
# Probe 127.0.0.1 with the proxy bypassed (Wait-ManagerHealth), not
# Invoke-WebRequest against 'localhost': the old check went through the system
# proxy and hit IPv6 ::1 first, so it stalled for ~90 s and then wrongly reported
# the manager "down" even though a browser could reach it (#135). The GUI URL
# shown to the user stays as localhost.
Write-Step 'Waiting for the manager to answer'
$started = Get-Date
$ready = Wait-ManagerHealth -Url "http://127.0.0.1:$webPort/api/health" -TimeoutSeconds 60
Write-Host ''
if ($ready) {
    Write-Ok "Manager is up at $url (after $([int]((Get-Date) - $started).TotalSeconds) s)"
} else {
    Write-Warn2 "Manager did not answer on $url yet - give it a moment, then refresh the page."
}

if (-not $NoBrowser) { Start-Process $url }

Write-Host ''
Write-Host "  Web GUI : $url   (user: admin, password: see .env)"
if ($managerVersion -eq 'latest') {
    Write-Host '  Version : latest (updates on every start; set MANAGER_VERSION in .env to lock it)'
} else {
    Write-Host "  Version : locked to $managerVersion (set MANAGER_VERSION=latest in .env to follow releases)"
}
Write-Host '  Running Arma servers keep running even if you close this window.'
Write-Host ''
Start-Sleep -Seconds 4
