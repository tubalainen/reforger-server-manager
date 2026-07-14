<#
.SYNOPSIS
    Starts Docker Desktop (and with it WSL2), brings the Reforger Server Manager
    up, and opens the web GUI. This is what the Desktop shortcut runs.

.DESCRIPTION
    Safe to run at any time: if everything is already running it just opens the GUI.
    Use -Update to pull the newest manager image first.
#>
[CmdletBinding()]
param(
    # Pull the latest manager image before starting.
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
if ($Update) {
    Write-Step 'Pulling the latest manager image'
    & $docker compose -f $Compose pull
    if ($LASTEXITCODE -ne 0) { Pause-OnExit 'docker compose pull failed - see the output above.' }
}

Write-Step 'Starting the manager'
& $docker compose -f $Compose up -d
if ($LASTEXITCODE -ne 0) { Pause-OnExit 'docker compose up failed - see the output above.' }

# --- 4. Wait for the API, then open the GUI ---------------------------------
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        if ((Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if ($ready) { Write-Ok "Manager is up at $url" } else { Write-Warn2 "Manager did not answer on $url yet - give it a moment." }

if (-not $NoBrowser) { Start-Process $url }

Write-Host ''
Write-Host "  Web GUI : $url   (user: admin, password: see .env)"
Write-Host '  Running Arma servers keep running even if you close this window.'
Write-Host ''
Start-Sleep -Seconds 4
