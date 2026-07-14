# Shared helpers for the Windows scripts. Dot-source it:  . "$PSScriptRoot\common.ps1"
#
# Every function here exists because the first version of these scripts got the
# same thing wrong three times: it asked Windows a question that always answers
# "yes" (see Test-WslInstalled), or it let a native command's stderr kill the
# script (see Invoke-Quiet).

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
    <#
    Run a command line through cmd.exe with its output discarded, and return
    $true when it succeeded.

    Why cmd.exe: with $ErrorActionPreference = 'Stop' (which these scripts set),
    redirecting a NATIVE command's stderr inside PowerShell 5.1 turns every stderr
    line into a terminating NativeCommandError. So the old

        & docker info 2>$null | Out-Null

    did not "quietly test whether Docker is up" — it CRASHED the script the moment
    Docker was down, which is the only case it was written to detect. Letting
    cmd.exe do the redirection means PowerShell never touches the stream.
    #>
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
    throw 'Docker Desktop is not installed. Run the installer from the README first.'
}

function Test-DockerEngine {
    param([string] $Cli)
    if (-not $Cli) { return $false }
    return (Invoke-Quiet "`"$Cli`" info")
}

function Test-WslInstalled {
    <#
    Is WSL actually installed?

    NOT the same question as "does wsl.exe exist". Windows 10/11 ship a wsl.exe
    STUB in System32 whether or not the feature is installed; running it on a
    machine without WSL just prints "wsl is not installed". The installer used to
    check `Get-Command wsl.exe`, always found the stub, reported "WSL is present"
    and skipped the install — so Docker Desktop then came up and complained "WSL
    not installed", and its engine never started (issue #51).

    `wsl --status` is the question that has a real answer.
    #>
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { return $false }
    return (Invoke-Quiet 'wsl --status')
}

function Wait-DockerEngine {
    <#
    Wait for the Docker engine, telling the user what to do if it stalls.

    On a first run Docker Desktop opens a sign-in screen and waits for a human.
    Nothing can start the engine until someone clicks Skip, so an installer that
    just spins for four minutes and then throws is useless: say what to click.
    #>
    param(
        [string] $Cli,
        [int] $TimeoutSeconds = 300,
        [string] $DockerDesktopExe = (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe')
    )

    if (Test-DockerEngine -Cli $Cli) {
        Write-Ok 'Docker engine already running'
        return $true
    }

    if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        if (-not (Test-Path $DockerDesktopExe)) {
            throw "Docker Desktop was not found at $DockerDesktopExe."
        }
        Write-Info 'Starting Docker Desktop...'
        Start-Process -FilePath $DockerDesktopExe
    }

    Write-Info 'Waiting for the Docker engine (a cold start takes a minute or two).'
    $waited = 0
    $hinted = $false
    while ($waited -lt $TimeoutSeconds) {
        Start-Sleep -Seconds 3
        $waited += 3
        if (Test-DockerEngine -Cli $Cli) {
            Write-Host ''
            Write-Ok "Docker engine running (after $waited s)"
            return $true
        }
        Write-Host '.' -NoNewline
        if (-not $hinted -and $waited -ge 45) {
            $hinted = $true
            Write-Host ''
            Write-Warn2 'Still waiting. Look at the Docker Desktop window:'
            Write-Host  '      * a "Sign in" screen? Click SKIP - you do NOT need a Docker account.' -ForegroundColor Yellow
            Write-Host  '      * terms to accept? Accept them.' -ForegroundColor Yellow
            Write-Host  '      Then leave it running; this will carry on by itself.' -ForegroundColor Yellow
            Write-Host  '    Still waiting' -NoNewline
        }
    }

    Write-Host ''
    Write-Warn2 "The Docker engine did not come up within $TimeoutSeconds seconds."
    Write-Info  'Open Docker Desktop, get it to say "Engine running" (clicking Skip on the'
    Write-Info  'sign-in screen if it is showing one), then run this again.'
    return $false
}
