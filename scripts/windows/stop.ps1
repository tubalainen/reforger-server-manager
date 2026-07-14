<#
.SYNOPSIS
    Stops the Reforger Server Manager container.

.DESCRIPTION
    Only the manager is stopped. Arma server instances are separate containers
    that keep running on their own - stop them from the GUI (or pass -All) if you
    want the machine quiet. Docker Desktop is left running.
#>
[CmdletBinding()]
param(
    # Also stop every Arma server instance the manager created.
    [switch] $All
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
. (Join-Path $here 'common.ps1')   # Get-DockerCli and friends live here now

$Compose = Join-Path $here 'docker-compose.windows.yaml'

$docker = Get-DockerCli

if ($All) {
    Write-Host 'Stopping the Arma server instances...' -ForegroundColor Cyan
    $ids = & $docker ps -q --filter 'label=reforger-manager.role=instance'
    foreach ($id in $ids) { & $docker stop $id | Out-Null }
    if ($ids) { Write-Host "    stopped $($ids.Count) instance container(s)" -ForegroundColor Green }
}

Write-Host 'Stopping the manager...' -ForegroundColor Cyan
& $docker compose -f $Compose stop
if ($LASTEXITCODE -ne 0) { throw 'docker compose stop failed.' }

Write-Host ''
Write-Host 'Stopped. Start it again from the Desktop shortcut.' -ForegroundColor Green
if (-not $All) {
    Write-Host 'Note: any running Arma servers are still up (they restart with Docker).' -ForegroundColor Yellow
    Write-Host '      Use  .\stop.ps1 -All  to stop those too.' -ForegroundColor Yellow
}
Write-Host ''
