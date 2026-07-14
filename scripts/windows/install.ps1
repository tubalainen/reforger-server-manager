<#
.SYNOPSIS
    Installs the Reforger Server Manager on Windows 10/11 (Docker Desktop + WSL2).

.DESCRIPTION
    Download this script, then run it from a normal (non-elevated) PowerShell window:

        $installer = "$env:TEMP\reforger-install.ps1"
        Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/install.ps1 -OutFile $installer
        powershell -ExecutionPolicy Bypass -File $installer

    Deliberately downloaded to a file rather than piped into the shell. Piping a
    remote script straight into the interpreter is the "ClickFix" pattern that
    malware uses, and Microsoft Defender blocks it on sight (Trojan:Win32/ClickFix)
    - it would also mean you run code you never got a chance to read. Options can
    simply be appended to the last line, e.g. -InstallDir 'D:\Reforger' -WebPort 8080.

    What it does:
      1. makes sure WSL2 and Docker Desktop are installed (installs them via winget if missing),
      2. creates an install folder with docker-compose.windows.yaml, .env and the start/stop scripts,
      3. generates a session secret and an admin password (or asks for one),
      4. opens the Windows firewall for the game + A2S UDP ranges (one elevation prompt),
      5. puts a "Reforger Server Manager" shortcut on the Desktop that starts Docker and the manager.

    Nothing else on the machine is touched, and re-running it is safe: an existing
    .env is never overwritten.
#>
[CmdletBinding()]
param(
    # Where the manager's compose file, .env and scripts are kept.
    [string] $InstallDir = (Join-Path $env:USERPROFILE 'ReforgerServerManager'),

    # Port the web GUI listens on, on this machine.
    [int] $WebPort = 7780,

    # Branch or tag of the repository to install from.
    [string] $Ref = 'main',

    # Skip the Docker Desktop / WSL2 checks (they are already known good).
    [switch] $SkipPrereqs,

    # Do not start the manager at the end.
    [switch] $NoStart
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # makes Invoke-WebRequest fast

$RepoRaw = "https://raw.githubusercontent.com/tubalainen/reforger-server-manager/$Ref"
$DockerDesktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'

function Write-Step  { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Warn2 { param($m) Write-Host "    [!]  $m" -ForegroundColor Yellow }
function Write-Info  { param($m) Write-Host "    $m" -ForegroundColor Gray }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DockerCli {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
    if (Test-Path $fallback) { return $fallback }
    return $null
}

function New-RandomString {
    param([int] $Length, [string] $Charset)
    $bytes = New-Object byte[] $Length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $out = ''
    foreach ($b in $bytes) { $out += $Charset[$b % $Charset.Length] }
    return $out
}

Write-Host ''
Write-Host '  Reforger Server Manager - Windows installer' -ForegroundColor White
Write-Host '  https://github.com/tubalainen/reforger-server-manager' -ForegroundColor DarkGray

# --- 1. Prerequisites: WSL2 + Docker Desktop --------------------------------
if (-not $SkipPrereqs) {
    Write-Step 'Checking Windows, WSL2 and Docker Desktop'

    if ([Environment]::OSVersion.Version.Build -lt 19044) {
        throw "Windows 10 21H2 (build 19044) or newer is required; this is build $([Environment]::OSVersion.Version.Build)."
    }
    Write-Ok "Windows build $([Environment]::OSVersion.Version.Build)"

    $needsReboot = $false

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        Write-Warn2 'WSL is not installed - installing it (a UAC prompt will appear)'
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList @(
            '-NoProfile', '-Command', 'wsl --install --no-distribution')
        $needsReboot = $true
    } else {
        Write-Ok 'WSL is present'
    }

    if (-not (Test-Path $DockerDesktopExe) -and -not (Get-DockerCli)) {
        Write-Warn2 'Docker Desktop is not installed'
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw ("winget is unavailable, so Docker Desktop cannot be installed automatically. " +
                   "Install it from https://www.docker.com/products/docker-desktop/ (keep the WSL2 " +
                   "option ticked), then run this installer again.")
        }
        Write-Info 'Installing Docker Desktop via winget - this takes a few minutes...'
        winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) {
            throw "winget could not install Docker Desktop (exit code $LASTEXITCODE). Install it manually from https://www.docker.com/products/docker-desktop/ and run this installer again."
        }
        $needsReboot = $true
    } else {
        Write-Ok 'Docker Desktop is present'
    }

    if ($needsReboot) {
        Write-Host ''
        Write-Host '  Restart Windows now, sign back in, and run this installer again' -ForegroundColor Yellow
        Write-Host '  to finish the setup. (WSL2 / Docker Desktop need the reboot.)' -ForegroundColor Yellow
        Write-Host ''
        return
    }
}

# --- 2. Install folder + files ----------------------------------------------
Write-Step "Setting up $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$files = @{
    'docker-compose.windows.yaml' = "$RepoRaw/docker-compose.windows.yaml"
    '.env.example'                = "$RepoRaw/.env.example"
    'start.ps1'                   = "$RepoRaw/scripts/windows/start.ps1"
    'stop.ps1'                    = "$RepoRaw/scripts/windows/stop.ps1"
    'firewall.ps1'                = "$RepoRaw/scripts/windows/firewall.ps1"
}
foreach ($name in $files.Keys) {
    $dest = Join-Path $InstallDir $name
    Invoke-WebRequest -Uri $files[$name] -OutFile $dest -UseBasicParsing
    Write-Ok $name
}

# --- 3. .env (never clobber an existing one) --------------------------------
$envPath = Join-Path $InstallDir '.env'
if (Test-Path $envPath) {
    Write-Step 'Keeping the existing .env'
    Write-Info 'Delete it and re-run the installer if you want a fresh configuration.'
    $generatedPassword = $null
} else {
    Write-Step 'Creating .env'

    Write-Host ''
    Write-Host '    Choose the password for the web GUI login (user: admin).' -ForegroundColor White
    Write-Host '    Press Enter to have a strong one generated for you.' -ForegroundColor Gray
    $secure = Read-Host '    Admin password' -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

    $generatedPassword = $null
    if ([string]::IsNullOrWhiteSpace($plain)) {
        $plain = New-RandomString -Length 18 -Charset 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        $generatedPassword = $plain
    }
    $secret = New-RandomString -Length 64 -Charset '0123456789abcdef'

    $content = Get-Content (Join-Path $InstallDir '.env.example') -Raw
    $content = $content -replace '(?m)^ADMIN_PASSWORD=.*$', ("ADMIN_PASSWORD=" + $plain)
    $content = $content -replace '(?m)^SESSION_SECRET=.*$', ("SESSION_SECRET=" + $secret)
    $content = $content -replace '(?m)^WEB_PORT=.*$',       ("WEB_PORT=" + $WebPort)
    Set-Content -Path $envPath -Value $content -Encoding ASCII
    Write-Ok ".env written (GUI on port $WebPort)"
}

# --- 4. Windows firewall: the player-facing UDP ranges ----------------------
Write-Step 'Opening the Windows firewall for the game + A2S UDP ports'

function Get-EnvValue {
    param([string] $Key, [string] $Default)
    $line = Select-String -Path $envPath -Pattern "^$Key=(.*)$" -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($line -and $line.Matches[0].Groups[1].Value.Trim()) {
        return $line.Matches[0].Groups[1].Value.Trim()
    }
    return $Default
}

$gameRange = Get-EnvValue -Key 'GAME_PORT_RANGE' -Default '2001-2020'
$a2sRange  = Get-EnvValue -Key 'A2S_PORT_RANGE'  -Default '17777-17796'
$firewallScript = Join-Path $InstallDir 'firewall.ps1'

# firewall.ps1 is elevated with -File, not as an encoded or generated command: an
# obfuscated elevated command line is exactly what antivirus heuristics look for,
# and it would hide from you what is about to run as administrator. Only the two
# player-facing ranges are opened - never RCON, never the web GUI.
$fwArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$firewallScript`"",
    '-GamePorts', $gameRange, '-A2sPorts', $a2sRange
)

if (Test-Admin) {
    & $firewallScript -GamePorts $gameRange -A2sPorts $a2sRange
} else {
    Write-Info 'Asking for administrator rights (firewall rules need them)...'
    $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $fwArgs
    if ($p.ExitCode -eq 0) {
        Write-Ok "UDP $gameRange and $a2sRange allowed inbound"
    } else {
        Write-Warn2 'The firewall rule was not created. Run this later in an ELEVATED PowerShell:'
        Write-Host "    powershell -ExecutionPolicy Bypass -File `"$firewallScript`"" -ForegroundColor Yellow
    }
}

# --- 5. Desktop shortcut ----------------------------------------------------
Write-Step 'Creating the Desktop shortcut'
$lnkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Reforger Server Manager.lnk'
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $InstallDir 'start.ps1')`""
$lnk.WorkingDirectory = $InstallDir
$lnk.Description = 'Start Docker and the Reforger Server Manager, then open the web GUI'
if (Test-Path $DockerDesktopExe) {
    $lnk.IconLocation = "$DockerDesktopExe,0"
} else {
    $lnk.IconLocation = "$(Join-Path $env:SystemRoot 'System32\shell32.dll'),13"
}
$lnk.Save()
Write-Ok 'Reforger Server Manager.lnk'

# --- 6. Done ----------------------------------------------------------------
Write-Host ''
Write-Host '  Installed.' -ForegroundColor Green
Write-Host ''
Write-Host "  Folder      : $InstallDir"
Write-Host "  Web GUI     : http://localhost:$WebPort"
Write-Host "  Username    : admin"
if ($generatedPassword) {
    Write-Host "  Password    : $generatedPassword" -ForegroundColor Yellow
    Write-Host '                ^ save this now - it is only shown here (it is also in .env).' -ForegroundColor Yellow
} else {
    Write-Host "  Password    : the one you chose (stored in $envPath)"
}
Write-Host ''
Write-Host '  Start it any time from the "Reforger Server Manager" shortcut on your Desktop.'
Write-Host '  Still to do, so players on the internet can join:'
Write-Host '    * forward UDP ' -NoNewline; Write-Host "$gameRange and $a2sRange" -NoNewline -ForegroundColor White
Write-Host " on your router to this PC's LAN IP (and reserve that IP in DHCP)."
Write-Host '    * never forward the RCON ports or the web GUI port.'
Write-Host ''

if (-not $NoStart) {
    $answer = Read-Host '  Start the manager now? [Y/n]'
    if ([string]::IsNullOrWhiteSpace($answer) -or $answer -match '^[Yy]') {
        & (Join-Path $InstallDir 'start.ps1')
    }
}
