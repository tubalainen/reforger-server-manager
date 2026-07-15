# Reforger Server Manager

A web-based manager for **Arma Reforger Dedicated Servers**. Draw up server templates
(scenario, mods, settings) in an intuitive web GUI, download the server files with
SteamCMD at the click of a button, and run any number of server instances — each in its
own Docker container — from a single `docker-compose.yaml`.

Runs on **Linux** (any VPS or home box) and on **Windows 10/11** via Docker Desktop,
where a PowerShell installer sets everything up and puts a start shortcut on your
Desktop. See [Getting started](#getting-started) or
[Running on Windows](#running-on-windows-11--10).

Inspired by (and a spiritual successor to) [Longbow / ArmaReforgerServerTool](https://github.com/soda3x/ArmaReforgerServerTool)
by soda3x — reimagined as a Dockerized web application.

> **Status: stable and feature-complete for day-to-day hosting.** Everything below is
> built and released: templates and the mod manager, SteamCMD downloads, multiple live
> server instances with logs, stats, scheduled restarts and crash recovery. Development
> continues in the open — see the [issues](https://github.com/tubalainen/reforger-server-manager/issues)
> and [releases](https://github.com/tubalainen/reforger-server-manager/releases).

Docker image: `ghcr.io/tubalainen/reforger-server-manager:latest`

## Features

- [x] Single `docker-compose.yaml` + single `.env` deployment, built-in login
- [x] One-click SteamCMD download of server files — **Stable** (app `1874900`) or
      **Experimental** (app `1890870`) — with live progress bars and streaming logs
- [x] Server templates: pick a scenario straight from the
      [Workshop](https://reforger.armaplatform.com/workshop), auto-resolve all mod
      dependencies, add extra mods, tune settings, save — and download the resulting
      `config.json`; or **upload an existing `config.json`** to pre-fill the wizard;
      the wizard shows the currently selected scenario and asks before a
      replace/remove drops the mods it brought in
- [x] Mod manager: search or paste-by-id to enable mods on top of a scenario —
      dependencies (and sub-dependencies) follow automatically; disabling a mod prompts
      whether to drop the dependencies it brought in; reorder mods and export/import the
      mod list as JSON; mods follow the latest Workshop release by default, or **lock any
      mod to a specific version** (only locked versions are written to `config.json`)
- [x] **Max players follows the scenario** — the wizard seeds the player limit from the
      count the scenario declares on the Workshop (a 12-player co-op scenario no longer
      gets a 64-slot server), and you can override it whenever you like
- [x] Multiple concurrent server instances (stable + experimental side by side), each a
      Docker container spawned and supervised by the manager
- [x] Live server logs in the browser (with a clear-window button), crash auto-restart
- [x] **Stored-data controls** per instance: see how much disk the baked mods, saved game
      and logs take, and clear any of them — wiping the baked mods makes the next start
      re-download and re-bake the template's current mod list
- [x] Scheduled restarts — set daily restart times per instance (server local time)
- [x] Live status per instance (players, FPS, CPU, memory) and a **Connect** address that
      auto-detects the server's public IP from its log when `PUBLIC_ADDRESS` isn't set
- [x] Honest server status: an instance reads **starting…** while the game server downloads
      mods and loads the world, and turns **online** only once its log says it is up and
      joinable — not the moment the container starts
- [x] Built-in **User Guide** page: getting-started walkthrough, feature guide, FAQ and
      links to the official Reforger documentation
- [x] **Windows 10/11 support** via Docker Desktop (WSL2): PowerShell installer, Desktop
      start shortcut, and a **Ports & firewall** panel that prints the exact firewall
      command for your configured port ranges

## Architecture

```
 Browser ──► manager container (FastAPI + Vue)
                │  docker.sock
                ├─► steamcmd container (one-shot downloads → shared volumes)
                └─► reforger-instance-* containers (one per server)
```

Only the manager lives in the compose file. Server instances and SteamCMD download jobs
are sibling containers the manager creates through the Docker API, attached to the same
Docker network and labeled so they survive manager restarts. Server files are downloaded
once per branch into local folders (`./serverfiles/stable`, `./serverfiles/experimental`)
and mounted read-only into each instance.

## How server instances run

Each instance is a sibling container created from `REFORGER_SERVER_IMAGE`
(ACE Mod's [arma-reforger](https://github.com/acemod/docker-reforger) image by
default). The manager:

- leases a unique host UDP port for game / A2S / RCON from the `.env` ranges and
  bakes them into a per-instance `config.json` rendered from the template;
- mounts the shared `./serverfiles/<branch>` install at `/reforger` so instances
  of the same branch reuse one download, plus per-instance `configs/`, `profile/`
  and `workshop/` folders under `./data/instances/<id>/`;
- labels the container so it is rediscovered after a manager restart, and
  restarts it automatically if it crashes (toggle per instance).

Stable and experimental servers can run side by side. Live server logs stream to
the instance detail page.

## Getting started

**On Windows?** Skip this section entirely — go to
[Running on Windows](#running-on-windows-11--10), where an installer does all of it for you.

On Linux, two paths, depending on how comfortable you are with Docker.

### New to Docker & Linux? (step-by-step)

**1 — Install Docker** on your Linux server with the official one-line convenience
script:

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Then let your user run Docker without `sudo`, and make sure the service starts on
boot:

```bash
sudo usermod -aG docker "$USER"      # log out and back in for this to take effect
sudo systemctl enable --now docker
```

Verify it works (after logging back in):

```bash
docker run --rm hello-world
```

**2 — Run this application.** You don't need the source code — just a folder with
the `docker-compose.yaml` and a `.env` file. Create a folder and download both:

```bash
mkdir reforger-server-manager && cd reforger-server-manager

# grab the compose file and an example .env into this folder
curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/docker-compose.yaml
curl -fsSL  https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/.env.example -o .env

# edit .env: at minimum set ADMIN_PASSWORD and SESSION_SECRET
nano .env

# pull the published image and start
docker compose pull
docker compose up -d
```

On the server itself, open `http://localhost:7780` and sign in with the credentials
from `.env`. (By default the GUI binds to `127.0.0.1` — see the security note under
[Quick start](#quick-start) before exposing it.)

**3 — Open the ports (NAT / port forwarding):**

For players on the internet to reach your server, you must open/forward ports
through your router (NAT) **and** any firewall on the Linux host, pointing them at
the Linux server's LAN IP:

- **Game port(s):** UDP `2001–2020` (default `GAME_PORT_RANGE`)
- **A2S query port(s):** UDP `17777–17796` (default `A2S_PORT_RANGE`)
- one game + A2S port pair per running server instance

Also set `PUBLIC_ADDRESS` in `.env` to your server's public IP so it advertises
correctly to the Arma backend. Keep the RCON ports (`19999–20018`) and the web GUI
(`7780`) **private** — do not forward them to the internet; reach the GUI through a
TLS reverse proxy or an SSH tunnel instead.

### Already comfortable with Docker? (quick version)

You only need Docker with the Compose plugin. Head to [Quick start](#quick-start)
below, fill in `.env`, `docker compose up -d`, and forward the UDP game/A2S ports
listed above. Put a TLS reverse proxy in front of the GUI for VPS use.

## Quick start

You only need the `docker-compose.yaml` and a `.env` — not the source. Create a
folder, drop both in, edit `.env`, then pull and start:

```bash
mkdir reforger-server-manager && cd reforger-server-manager
curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/docker-compose.yaml
curl -fsSL  https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/.env.example -o .env
# edit .env: set ADMIN_PASSWORD and SESSION_SECRET
docker compose pull
docker compose up -d
```

Open `http://localhost:7780` and sign in with the credentials from `.env`.
No other host setup is needed: the `data/` and `serverfiles/` folders are created on
first run, and the container fixes their ownership itself before dropping privileges
to an unprivileged user (uid 1000).

By default the GUI binds to `127.0.0.1` — put a reverse proxy (nginx/Caddy) with TLS in
front for VPS use, or set `WEB_BIND=0.0.0.0` at your own risk (login is still required).

> **Security note:** the manager mounts `/var/run/docker.sock`, which is root-equivalent
> on the host — that is what lets it create server containers. Treat the web GUI
> credentials accordingly and firewall the port.
>
> The built-in login can be turned off with `AUTH_ENABLED=false` so a reverse proxy
> (NGINX, Caddy, Authelia, …) can enforce authentication instead. Only do this when
> such a proxy is actually in front of the app — with it off and no proxy, the GUI
> (and thus Docker) is completely open.

### First run

On the **Downloads** tab, do both one-time steps before creating a server instance:

1. **Pull the server runtime image** — the Docker image each instance runs from
   (`REFORGER_SERVER_IMAGE`). `docker compose up` only pulls the manager itself, so the
   runtime image must be fetched once here.
2. **Download the server files** for the branch you want (Stable / Experimental) — the
   ~10 GB of game data mounted into every instance of that branch.

Then head to **Instances** and create your first server from a template.

## Running on Windows 11 / 10

Windows is supported through **Docker Desktop with its WSL2 backend** — the same
manager image, the same Arma server containers, running inside the lightweight Linux
VM that Docker Desktop manages for you. You never have to touch WSL yourself.

### Install

Open **PowerShell** (a normal window — it asks for admin only when it needs to), then
copy-paste these three lines. They download the installer, then run it:

```powershell
$installer = "$env:TEMP\reforger-install.ps1"
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/install.ps1 -OutFile $installer
powershell -ExecutionPolicy Bypass -File $installer
```

Append options to the last line if you want them, e.g. `-InstallDir 'D:\Reforger'` or
`-WebPort 8080`.

> **Why not a single `irm … | iex` line?** Because piping a downloaded script straight
> into the interpreter is the *ClickFix* technique that real malware uses, so Microsoft
> Defender blocks it as `Trojan:Win32/ClickFix` — and it would run code you never had a
> chance to read. Downloading to a file first keeps Defender happy and lets you open
> `%TEMP%\reforger-install.ps1` in Notepad and check it before it runs. Do that: never
> take our word for what a script does.

That is the only command you run. The installer:

1. **installs WSL2** if it is missing, then **reboots and carries on by itself** — you
   are asked first, and after you sign back in the installer reopens and finishes.
   (Docker Desktop cannot run without WSL2, and Windows can only finish installing it
   across a restart.)
2. **installs Docker Desktop** if it is missing, straight from Docker's own installer,
   silently and with the licence pre-accepted and the WSL2 backend selected;
3. creates `%USERPROFILE%\ReforgerServerManager` with `docker-compose.windows.yaml`,
   a `.env`, and the start/stop/firewall scripts;
4. generates a `SESSION_SECRET` and lets you pick (or auto-generates) the admin
   password — it is printed once at the end and stored in `.env`;
5. opens the **Windows firewall** for the game + A2S UDP ranges by running
   `firewall.ps1` elevated (one UAC prompt — you can read that script first too);
6. puts a **Reforger Server Manager** shortcut on your Desktop.

> **The one thing you may have to click.** The very first time Docker Desktop opens it
> shows a **Sign in** screen. Click **Skip** — you do not need a Docker account, and
> nothing here uses one. The installer waits for you and says so on screen. Docker's
> engine will not start until that screen is dismissed, which is the only manual step
> in the whole process.

The shortcut is all you need from then on: it starts Docker Desktop (and with it the
WSL2 VM), waits for the engine, brings the manager up with the right networking, and
opens `http://localhost:7780`. Options if you prefer to drive it by hand:

```powershell
cd $env:USERPROFILE\ReforgerServerManager
.\start.ps1            # pull the newest manager image (unless locked), then start
.\start.ps1 -NoUpdate  # start the version already on disk, no pull (e.g. offline)
.\stop.ps1             # stop the manager (running Arma servers stay up)
.\stop.ps1 -All        # also stop every Arma server instance
```

Then do the [First run](#first-run) steps in the GUI (pull the runtime image, download
the server files) and create an instance.

### Updating (and locking a version)

**The manager updates itself on start.** Every time you launch it (the Desktop shortcut,
or `start.ps1`), it pulls the newest manager image first, so you are always on the latest
release. Your data, settings and running Arma servers are untouched. To start without
pulling — offline, or just faster — use `.\start.ps1 -NoUpdate`.

**To lock the manager to one version,** set `MANAGER_VERSION` in
`$env:USERPROFILE\ReforgerServerManager\.env` to a release tag, then restart it:

```powershell
# in .env — pin to a release so it never changes:
MANAGER_VERSION=v0.31.0
# ...or follow every release (the default):
MANAGER_VERSION=latest
```

Release tags are listed on the
[Releases page](https://github.com/tubalainen/reforger-server-manager/releases). With a
version pinned, start still confirms that exact image is present but never moves you
forward until you change the value back.

**To update the scripts and compose file themselves** (not just the image) — for instance
when a release changes how the stack is wired — re-run the installer. It refreshes the
files in place and **keeps your existing `.env`**:

```powershell
$installer = "$env:TEMP\reforger-install.ps1"
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/install.ps1 -OutFile $installer
powershell -ExecutionPolicy Bypass -File $installer
```

The Arma **server runtime image** and the **server files** are updated separately from the
GUI, on the [First run](#first-run) / Downloads screens — pull the runtime image and
re-download the branch when you want those refreshed.

### Uninstalling (or starting over after a failed install)

```powershell
cd $env:USERPROFILE\ReforgerServerManager
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

If the install folder is gone or the install never finished, fetch the uninstaller and
run it from anywhere — it finds whatever is there:

```powershell
$u = "$env:TEMP\reforger-uninstall.ps1"
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/uninstall.ps1 -OutFile $u
powershell -ExecutionPolicy Bypass -File $u
```

It lists exactly what it found and what it will do, and only proceeds when you type
`REMOVE`. It removes the containers, the install folder (including the `.env` with your
admin password), the Desktop shortcut, the firewall rule and the installer's own
leftovers.

**Your data is kept by default.** Templates, instances, saved games and the ~10 GB of
downloaded server files live in Docker volumes, which are left alone — reinstall and
they come back. Add `-RemoveData` to delete them too (irreversible), and `-RemoveImages`
to drop the Docker images as well.

**Docker Desktop and WSL2 are never touched** — other things on your machine may be
using them. Remove them from *Add or remove programs* if you want them gone.

### Where the files live

The Windows compose file keeps state in **Docker named volumes**
(`reforger-data`, `reforger-serverfiles-stable`, `reforger-serverfiles-experimental`),
not in a Windows folder. The ~10 GB of server files are Linux-owned and heavily read
during startup: on the VM's native ext4 disk that is fast and permission-clean, while
on an Explorer folder it would be slow (9p) and prone to ownership errors. Browse or
back them up from **Docker Desktop → Volumes**.

### Letting players in

Two hops have to be open — Windows and your router. Both use the same UDP ranges, and
the GUI prints the exact command for you under **Instances → Ports & firewall**.

1. **Windows firewall** — the installer already did this. To redo it (after changing the
   ranges in `.env`, say), run either of these in an *elevated* PowerShell:

   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\ReforgerServerManager\firewall.ps1" -GamePorts 2001-2020 -A2sPorts 17777-17796
   # ...or the rule it creates, directly:
   New-NetFirewallRule -DisplayName "Arma Reforger (game + A2S)" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 2001-2020,17777-17796
   ```

2. **Router:** forward UDP `2001-2020` and `17777-17796` to this PC's LAN IP, and give
   that PC a **DHCP reservation** so the address never moves. Set `PUBLIC_ADDRESS` in
   `.env` to your public IP.

Never forward the RCON ports (`19999-20018`) or the web GUI port (`7780`).

### Keeping it running

- Server containers already carry a restart policy, so they come back with Docker.
- In **Docker Desktop → Settings → General**, tick **Start Docker Desktop when you sign
  in** so a reboot brings everything back by itself.
- Docker Desktop runs inside your *user session*, not as a service. On an unattended
  machine that means the box must actually sign in after a reboot — configure automatic
  sign-in (`netplwiz`, untick "Users must enter a user name and password") or expect to
  log in manually before the servers come back.

### Do not run Docker CE inside WSL

Installing Docker Engine *inside* a WSL distro instead of using Docker Desktop looks
tempting, but its NAT only forwards TCP: the published **UDP** game/A2S ports never
reach the Windows host, and `netsh portproxy` cannot help (it is TCP-only). Players
would never see or join your server. Use Docker Desktop.

## Development

Everything runs in Docker — there is nothing to install on the host:

```bash
# Build and run from source: comment 'image:', uncomment 'build:' in docker-compose.yaml
docker compose up --build -d

# Run the backend test suite in a container
docker build --target test .
```

For frontend work with hot reload (Node 20+; proxies `/api` to the running manager):

```bash
cd frontend
npm install
npm run dev
```

## Credits & sources

This project stands on the shoulders of the Arma Reforger community. Inspiration and
reference material used in its design:

**The original tool this project succeeds**
- [soda3x/ArmaReforgerServerTool (Longbow)](https://github.com/soda3x/ArmaReforgerServerTool) — the Windows GUI whose feature set (scenario selection, mod list import/export, crash monitoring, scheduled restarts) defines this project's scope

**Reforger dedicated-server container images** (the manager spawns instances from one of these; inspiration for the container architecture)
- [acemod/docker-reforger](https://github.com/acemod/docker-reforger) — default instance image; env-driven config, stable/experimental via `STEAM_APPID`
- [RouHim/arma-reforger-container-image](https://github.com/RouHim/arma-reforger-container-image) — compose-first design, mounted `config.json`
- [jsknnr/arma-reforger-server](https://github.com/jsknnr/arma-reforger-server), [Kexanone/reforger-server](https://github.com/Kexanone/reforger-server), [soda3x/docker-reforger-server](https://github.com/soda3x/docker-reforger-server), [zuwarm/reforger-server](https://github.com/zuwarm/reforger-server) — alternative images surveyed for the architecture
- [steamcmd/steamcmd](https://hub.docker.com/r/steamcmd/steamcmd) — official SteamCMD image used for one-shot server-file downloads

**Workshop data** (no official API exists; these prove the scraping approach)
- [Arma Reforger Workshop](https://reforger.armaplatform.com/workshop) — the source of scenario, mod and dependency metadata
- [SirFrostingham/Arma-Reforger-Workshop-Mods-Dependencies-Downloader](https://github.com/SirFrostingham/Arma-Reforger-Workshop-Mods-Dependencies-Downloader) and [SowinskiBraeden/ReforgerWorkshopAPI](https://github.com/SowinskiBraeden/ReforgerWorkshopAPI) — community dependency-resolution tools

**Official documentation**
- [Bohemia Interactive — Arma Reforger: Server Config](https://community.bistudio.com/wiki/Arma_Reforger:Server_Config) — the `config.json` schema templates render to
- [Bohemia Interactive — Arma Reforger: Server Hosting](https://community.bistudio.com/wiki/Arma_Reforger:Server_Hosting) — Steam app IDs (stable `1874900`, experimental `1890870`) and hosting guidance

## Disclaimer

This application has been **fully developed using Claude AI** (Anthropic). As such,
it may contain bugs, defects, or unexpected behaviour. It is provided **"as is",
without warranty of any kind**, express or implied.

The repository owner accepts **no responsibility or liability** for any damage, data
loss, downtime, security issues, or other consequences arising from the use,
misuse, or inability to use this application/setup. **You use it entirely at your
own risk.** Always review the code, secure your deployment, and keep backups before
running it in any environment you care about.

## License

MIT
