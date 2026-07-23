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

function Test-ManagerHealth {
    <#
    Return $true when the manager's HTTP API answers 200 at $Url.

    Deliberately NOT Invoke-WebRequest. On Windows that call is routed through the
    system WinINET proxy (including WPAD auto-detect), which usually does NOT
    bypass localhost - so a manager a browser reaches fine gets reported as "not
    answering", after a long per-request stall while the proxy is probed (#135).
    A raw HttpWebRequest with Proxy = $null connects straight to the loopback
    address. Call it against 127.0.0.1, not 'localhost': Windows resolves
    'localhost' to IPv6 ::1 first, where Docker Desktop's published port may not
    be listening, giving the same false "down" result.
    #>
    param(
        [Parameter(Mandatory)][string] $Url,
        [int] $TimeoutMs = 2000
    )
    $resp = $null
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Proxy = $null          # direct connection - never via a system proxy
        $req.Method = 'GET'
        $req.Timeout = $TimeoutMs
        $resp = $req.GetResponse()
        return ([int]$resp.StatusCode -eq 200)
    } catch {
        return $false
    } finally {
        if ($resp) { $resp.Close() }
    }
}

function Update-ManagerScripts {
    <#
    Refresh the local copy of the manager's PowerShell scripts from GitHub, so a
    Desktop-shortcut launch picks up script fixes without the user having to
    re-run the installer (#135 follow-up: start.ps1 pulled the manager IMAGE but
    never itself). Returns the list of file names whose contents changed (an empty
    list when everything is already current), or $null when the check could not
    run at all (offline) - in which case the caller keeps the scripts on disk.

    Only the scripts are refreshed, always from `main`: they are host-side tooling
    that just starts Docker and runs compose, so they stay compatible with any
    pinned MANAGER_VERSION. The compose file and .env are deliberately left alone
    (.env is the user's config; the compose file the installer owns).

    All files are downloaded to temp first and only swapped in when every one
    arrived, so a dropped connection never leaves a half-updated script set.
    Change detection is by SHA-256; the installer and this function both write raw
    bytes (LF, per .gitattributes), so an unchanged file hashes identically.
    #>
    param(
        [Parameter(Mandatory)][string] $InstallDir,
        [string] $Ref = 'main'
    )
    $repoRaw = "https://raw.githubusercontent.com/tubalainen/reforger-server-manager/$Ref/scripts/windows"
    $names = @('start.ps1', 'stop.ps1', 'firewall.ps1', 'common.ps1', 'uninstall.ps1')

    # Phase 1: fetch every script to a temp file; abandon quietly if any fails.
    $temps = @{}
    foreach ($name in $names) {
        $tmp = Join-Path $InstallDir ($name + '.new')
        try {
            Invoke-WebRequest -Uri "$repoRaw/$name" -OutFile $tmp -UseBasicParsing -TimeoutSec 20
            $temps[$name] = $tmp
        } catch {
            foreach ($t in $temps.Values) { Remove-Item $t -Force -ErrorAction SilentlyContinue }
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            return $null
        }
    }

    # Phase 2: swap in only the ones whose contents actually changed.
    $changed = @()
    foreach ($name in $names) {
        $dest = Join-Path $InstallDir $name
        $tmp = $temps[$name]
        $newHash = (Get-FileHash -Path $tmp -Algorithm SHA256).Hash
        $oldHash = if (Test-Path $dest) { (Get-FileHash -Path $dest -Algorithm SHA256).Hash } else { '' }
        if ($newHash -ne $oldHash) {
            try {
                Move-Item -Path $tmp -Destination $dest -Force
                $changed += $name
            } catch {
                Write-Warn2 "Could not update $name ($($_.Exception.Message)) - keeping the current one."
                Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            }
        } else {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        }
    }
    return ,$changed
}

function Wait-ManagerHealth {
    <#
    Poll the manager until it answers or the budget runs out, printing a progress
    dot per attempt so a slow first boot doesn't look like a hang. Returns $true
    once it answers. Uses a wall-clock deadline so a stalled attempt can't push
    the total far past the budget.
    #>
    param(
        [Parameter(Mandatory)][string] $Url,
        [int] $TimeoutSeconds = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ManagerHealth -Url $Url) { return $true }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 2
    }
    return $false
}
