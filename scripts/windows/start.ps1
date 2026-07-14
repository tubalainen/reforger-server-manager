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
    [int] $DockerTimeout = 240
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$Compose = Join-Path $here 'docker-compose.windows.yaml'
$EnvFile = Join-Path $here '.env'
$DockerDesktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Warn2{ param($m) Write-Host "    [!]  $m" -ForegroundColor Yellow }

function Get-DockerCli {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
    if (Test-Path $fallback) { return $fallback }
    throw 'Docker Desktop is not installed. Re-run the installer one-liner from the README.'
}

function Test-DockerEngine {
    param([string] $Cli)
    & $Cli info --format '{{.ServerVersion}}' 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

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

Write-Host ''
Write-Host '  Reforger Server Manager' -ForegroundColor White

if (-not (Test-Path $Compose)) {
    throw "docker-compose.windows.yaml is missing from $here - re-run the installer."
}

$docker = Get-DockerCli
$webPort = Get-EnvValue -Key 'WEB_PORT' -Default '7780'
$url = "http://localhost:$webPort"

# --- 1. Docker engine (starting Docker Desktop also starts the WSL2 VM) -----
Write-Step 'Starting Docker'
if (Test-DockerEngine -Cli $docker) {
    Write-Ok 'Docker engine already running'
} else {
    if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path $DockerDesktopExe)) {
            throw "Docker Desktop was not found at $DockerDesktopExe."
        }
        Start-Process -FilePath $DockerDesktopExe
    }
    Write-Host '    Waiting for the Docker engine (this takes a while on a cold boot)' -NoNewline
    $waited = 0
    while (-not (Test-DockerEngine -Cli $docker)) {
        if ($waited -ge $DockerTimeout) {
            Write-Host ''
            throw ("The Docker engine did not come up within $DockerTimeout seconds. Open Docker " +
                   "Desktop, wait for it to say 'Engine running', then run this shortcut again.")
        }
        Start-Sleep -Seconds 3
        $waited += 3
        Write-Host '.' -NoNewline
    }
    Write-Host ''
    Write-Ok "Docker engine running (after $waited s)"
}

# --- 2. Firewall sanity check (the installer creates this rule) -------------
$ruleName = 'Arma Reforger (game + A2S)'
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    $game = Get-EnvValue -Key 'GAME_PORT_RANGE' -Default '2001-2020'
    $a2s  = Get-EnvValue -Key 'A2S_PORT_RANGE'  -Default '17777-17796'
    Write-Warn2 'The firewall rule for the game ports is missing - players will not be able to join.'
    Write-Host  '    Run this once in an ELEVATED PowerShell:' -ForegroundColor Yellow
    Write-Host  "    New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Action Allow -Protocol UDP -LocalPort $game,$a2s" -ForegroundColor Yellow
}

# --- 3. The manager itself ---------------------------------------------------
if ($Update) {
    Write-Step 'Pulling the latest manager image'
    & $docker compose -f $Compose pull
    if ($LASTEXITCODE -ne 0) { throw 'docker compose pull failed.' }
}

Write-Step 'Starting the manager'
& $docker compose -f $Compose up -d
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed - see the output above.' }

# --- 4. Wait for the API, then open the GUI ---------------------------------
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ready = $true; break }
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
