<#
.SYNOPSIS
    Allows inbound UDP on the Arma Reforger game + A2S port ranges.

.DESCRIPTION
    Run from an ELEVATED PowerShell (firewall changes need administrator rights).
    The installer runs this for you; you can also run it by hand at any time:

        powershell -ExecutionPolicy Bypass -File .\firewall.ps1 -GamePorts 2001-2020 -A2sPorts 17777-17796

    Only the two player-facing ranges are opened. RCON and the web GUI are never
    opened here: neither belongs on the internet.

    This is a separate, readable script on purpose - the installer elevates it with
    -File instead of passing an encoded command, so you can always see exactly what
    is about to run as administrator.
#>
[CmdletBinding()]
param(
    # UDP range players connect on (GAME_PORT_RANGE in .env).
    [string] $GamePorts = '2001-2020',

    # UDP range the server browser queries (A2S_PORT_RANGE in .env).
    [string] $A2sPorts = '17777-17796',

    [string] $RuleName = 'Arma Reforger (game + A2S)'
)

$ErrorActionPreference = 'Stop'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw 'This script must run in an elevated PowerShell (Run as administrator).'
}

if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $RuleName
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol UDP `
    -LocalPort $GamePorts, $A2sPorts | Out-Null

Write-Host "Firewall rule '$RuleName' now allows inbound UDP $GamePorts and $A2sPorts." -ForegroundColor Green
