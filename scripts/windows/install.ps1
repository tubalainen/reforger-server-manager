<#
.SYNOPSIS
    Installs the Reforger Server Manager on Windows 10/11 (Docker Desktop + WSL2).

.DESCRIPTION
    Download this script, then run it from a normal (non-elevated) PowerShell window:

        $installer = "$env:TEMP\reforger-install.ps1"
        Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/install.ps1 -OutFile $installer
        powershell -ExecutionPolicy Bypass -File $installer

    Deliberately downloaded to a file rather than piped into the shell: piping a
    remote script straight into the interpreter is the "ClickFix" pattern malware
    uses, Microsoft Defender blocks it, and it would mean running code you never
    got a chance to read.

    It installs WSL2 and Docker Desktop if they are missing, sets the manager up,
    opens the firewall for the game ports, and puts a shortcut on your Desktop.

    If WSL2 has to be installed, Windows must reboot. The script offers to reboot
    and CONTINUE BY ITSELF afterwards, so you only ever run one command.
#>
[CmdletBinding()]
param(
    # Where the manager's compose file, .env and scripts are kept.
    [string] $InstallDir = (Join-Path $env:USERPROFILE 'ReforgerServerManager'),

    # Port the web GUI listens on, on this machine.
    [int] $WebPort = 7780,

    # Branch or tag of the repository to install from.
    [string] $Ref = 'main',

    # Skip the WSL2 / Docker Desktop checks (they are already known good).
    [switch] $SkipPrereqs,

    # Do not start the manager at the end.
    [switch] $NoStart,

    # Set when the script resumes itself after the reboot it asked for.
    [switch] $Resumed
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # makes Invoke-WebRequest fast

$RepoRaw = "https://raw.githubusercontent.com/tubalainen/reforger-server-manager/$Ref"
$DockerDesktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
$DockerInstallerUrl = 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'
$SelfPath = $MyInvocation.MyCommand.Path

# The helpers live next to this script once installed, but on the very first run
# this file is alone in %TEMP% — so fetch common.ps1 beside it if it is not there.
$CommonPath = Join-Path (Split-Path -Parent $SelfPath) 'common.ps1'
if (-not (Test-Path $CommonPath)) {
    Invoke-WebRequest -Uri "$RepoRaw/scripts/windows/common.ps1" -OutFile $CommonPath -UseBasicParsing
}
. $CommonPath

function New-RandomString {
    param([int] $Length, [string] $Charset)
    $bytes = New-Object byte[] $Length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $out = ''
    foreach ($b in $bytes) { $out += $Charset[$b % $Charset.Length] }
    return $out
}

function Register-Resume {
    <#
    Continue this installer automatically after the reboot WSL2 needs, so the user
    runs ONE command instead of "run, reboot, remember to run it again".
    RunOnce fires once, at the next sign-in, and then deletes itself.
    #>
    param([string] $ScriptPath)
    $resumeDir = Join-Path $env:LOCALAPPDATA 'ReforgerServerManager'
    New-Item -ItemType Directory -Force -Path $resumeDir | Out-Null
    $resumeScript = Join-Path $resumeDir 'resume-install.ps1'
    Copy-Item -Path $ScriptPath -Destination $resumeScript -Force
    Copy-Item -Path $CommonPath -Destination (Join-Path $resumeDir 'common.ps1') -Force

    $cmd = ('powershell -NoProfile -ExecutionPolicy Bypass -File "{0}" -InstallDir "{1}" -WebPort {2} -Ref "{3}" -Resumed' -f
            $resumeScript, $InstallDir, $WebPort, $Ref)
    Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' `
                     -Name 'ReforgerServerManagerInstall' -Value $cmd
}

Write-Host ''
Write-Host '  Reforger Server Manager - Windows installer' -ForegroundColor White
Write-Host '  https://github.com/tubalainen/reforger-server-manager' -ForegroundColor DarkGray
if ($Resumed) { Write-Info 'Continuing after the reboot.' }

# --- 1. Prerequisites: WSL2 + Docker Desktop --------------------------------
if (-not $SkipPrereqs) {
    Write-Step 'Checking Windows, WSL2 and Docker Desktop'

    if ([Environment]::OSVersion.Version.Build -lt 19044) {
        throw "Windows 10 21H2 (build 19044) or newer is required; this is build $([Environment]::OSVersion.Version.Build)."
    }
    Write-Ok "Windows build $([Environment]::OSVersion.Version.Build)"

    # WSL2. NOTE: the presence of wsl.exe proves nothing — Windows ships a stub of
    # it either way, which is why the first version of this installer wrongly
    # reported "WSL is present" and left Docker Desktop unable to start (#51).
    if (Test-WslInstalled) {
        Write-Ok 'WSL2 is installed'
        Write-Info 'Making sure the WSL kernel is current...'
        $null = Invoke-Quiet 'wsl --update'
    } else {
        Write-Warn2 'WSL2 is NOT installed (Docker Desktop cannot run without it) - installing it now.'
        Write-Info  'A UAC prompt will appear. This needs a reboot afterwards.'
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -ArgumentList @(
            '-NoProfile', '-Command', 'wsl --install --no-distribution; wsl --update')

        Write-Host ''
        Write-Host '  Windows must restart to finish installing WSL2.' -ForegroundColor Yellow
        $answer = Read-Host '  Restart now and let the installer carry on by itself afterwards? [Y/n]'
        if ([string]::IsNullOrWhiteSpace($answer) -or $answer -match '^[Yy]') {
            Register-Resume -ScriptPath $SelfPath
            Write-Info 'Restarting. This window will reopen after you sign back in.'
            Start-Sleep -Seconds 3
            Restart-Computer -Force
            return
        }
        Write-Warn2 'Reboot when you can, then run this installer again to finish.'
        return
    }

    # Docker Desktop. Installed straight from Docker's own installer so we can pass
    # --accept-license and pin the WSL2 backend, which removes the licence prompt
    # from the user's first run.
    if (-not (Test-Path $DockerDesktopExe) -and -not (Get-DockerCli -Quiet)) {
        Write-Warn2 'Docker Desktop is not installed - installing it (a few minutes).'
        $dockerSetup = Join-Path $env:TEMP 'DockerDesktopInstaller.exe'
        try {
            Write-Info 'Downloading Docker Desktop from docker.com...'
            Invoke-WebRequest -Uri $DockerInstallerUrl -OutFile $dockerSetup -UseBasicParsing
            Write-Info 'Installing (silent, licence accepted, WSL2 backend)...'
            $p = Start-Process -FilePath $dockerSetup -Verb RunAs -Wait -PassThru -ArgumentList @(
                'install', '--quiet', '--accept-license', '--backend=wsl-2')
            if ($p.ExitCode -ne 0) {
                throw "the Docker Desktop installer exited with code $($p.ExitCode)"
            }
        } catch {
            throw ("Could not install Docker Desktop automatically ($($_.Exception.Message)). " +
                   "Install it by hand from https://www.docker.com/products/docker-desktop/ " +
                   "(keep the WSL2 option ticked), then run this installer again.")
        } finally {
            Remove-Item $dockerSetup -Force -ErrorAction SilentlyContinue
        }
        Write-Ok 'Docker Desktop installed'
    } else {
        Write-Ok 'Docker Desktop is present'
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
    'common.ps1'                  = "$RepoRaw/scripts/windows/common.ps1"
    'uninstall.ps1'               = "$RepoRaw/scripts/windows/uninstall.ps1"
}
foreach ($name in $files.Keys) {
    Invoke-WebRequest -Uri $files[$name] -OutFile (Join-Path $InstallDir $name) -UseBasicParsing
    Write-Ok $name
}

# --- 3. .env (never clobber an existing one) --------------------------------
$envPath = Join-Path $InstallDir '.env'
$generatedPassword = $null
if (Test-Path $envPath) {
    Write-Step 'Keeping the existing .env'
    Write-Info 'Delete it and re-run the installer if you want a fresh configuration.'
} else {
    Write-Step 'Creating .env'
    Write-Host ''
    Write-Host '    Choose the password for the web GUI login (user: admin).' -ForegroundColor White
    Write-Host '    Press Enter to have a strong one generated for you.' -ForegroundColor Gray
    $secure = Read-Host '    Admin password' -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

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
# obfuscated elevated command line is what antivirus heuristics look for, and it
# would hide from you what is about to run as administrator.
if (Test-Admin) {
    & $firewallScript -GamePorts $gameRange -A2sPorts $a2sRange
} else {
    Write-Info 'Asking for administrator rights (firewall rules need them)...'
    $p = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$firewallScript`"",
        '-GamePorts', $gameRange, '-A2sPorts', $a2sRange)
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
Write-Host "  To remove it later:  powershell -ExecutionPolicy Bypass -File `"$(Join-Path $InstallDir 'uninstall.ps1')`""
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

if ($Resumed) {
    Write-Host ''
    Read-Host '  Press Enter to close this window'
}
