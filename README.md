# Reforger Server Manager

A web-based manager for **Arma Reforger Dedicated Servers**. Draw up server templates
(scenario, mods, settings) in an intuitive web GUI, download the server files with
SteamCMD at the click of a button, and run any number of server instances — each in its
own Docker container — from a single `docker-compose.yaml`.

Runs on **Linux** (any VPS or home box) and on **Windows 10/11** via Docker Desktop,
where a PowerShell installer sets everything up and puts a start shortcut on your
Desktop. See [Installation](#installation) or
[Running on Windows](#3-windows-10-and-11).

Inspired by (and a spiritual successor to) [Longbow / ArmaReforgerServerTool](https://github.com/soda3x/ArmaReforgerServerTool)
by soda3x — reimagined as a Dockerized web application.

> **Status: stable and feature-complete for day-to-day hosting.** Everything below is
> built and released: templates and the mod manager, SteamCMD downloads, multiple live
> server instances with logs, stats, scheduled restarts and crash recovery. Development
> continues in the open — see the [issues](https://github.com/tubalainen/reforger-server-manager/issues)
> and [releases](https://github.com/tubalainen/reforger-server-manager/releases).

Docker image: `ghcr.io/tubalainen/reforger-server-manager:latest`

## Screenshots

<p align="center">
  <img src="docs/images/01-server-templates.png" alt="The Server Templates list" width="820"><br>
  <em>The template library — each template bundles a scenario, its mods and every server
  setting. Edit one, download its <code>config.json</code>, or delete it.</em>
</p>

<p align="center">
  <img src="docs/images/02-template-scenario.png" alt="Editing a template: the Scenario step" width="820"><br>
  <em>Editing a template: pick a scenario straight from the Workshop, with a live
  <code>config.json</code> preview beside every step.</em>
</p>

<p align="center">
  <img src="docs/images/03-template-mods.png" alt="The mod manager" width="820"><br>
  <em>The mod manager: enable mods on top of the scenario — dependencies are pulled in and
  labelled automatically, and any mod can be locked to a specific version.</em>
</p>

<p align="center">
  <img src="docs/images/04-template-settings.png" alt="The template settings step" width="820"><br>
  <em>Every server setting in one place — player limit, view distances, VON, persistence,
  RCON and the full set of engine launch parameters.</em>
</p>

<p align="center">
  <img src="docs/images/05-instance-detail.png" alt="The Server Instances page" width="820"><br>
  <em>The Server Instances page: a live status bar and per-instance controls (start/stop,
  restart, logs) up top, and one-click SteamCMD server-file downloads and the runtime-image
  pull below.</em>
</p>

## Features

- [x] Single `docker-compose.yaml` + single `.env` deployment, built-in login
- [x] **Hardened by default** — the Docker socket is never mounted into the app (a
      least-privilege proxy fronts it), containers run with `no-new-privileges`, and
      the manager refuses to start exposed on a default password. The VPS setup adds
      automatic HTTPS, and one command signs every session out if a cookie leaks
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
- [x] **Edit `config.json` by hand** — the preview panel flips into a real JSON editor
      (syntax highlighting, line numbers, indent guides, live error marks). Keys the GUI
      knows flow back into the wizard's fields; **any key it doesn't know — including
      scenario-specific `gameProperties` — is kept and re-applied on every future save**.
      Validation blocks broken JSON and out-of-range values, but never blocks a custom key
- [x] **Mods Overview** — one persistent list of every mod ever baked into a template, shown
      as a tree with its live-resolved dependencies. Each mod is highlighted where it's baked
      &amp; downloaded to a server (with version); a mod stays on the list even after its
      template is gone. Tick mods at any level and **add them to a template in one click**;
      pin mods as **persist** so a prune never removes them, and **Clear &amp; rescan** to
      drop the ones no template uses any more
- [x] **Admins, whitelist and ban list** — name the server's admins (Steam64 or Bohemia
      identity id; they `#login` without the admin password and skip the join queue), and
      keep a whitelist and a ban list of identity ids. The wizard checks each id as you
      type, takes a whole pasted batch at once, warns past the 20 admins the game applies,
      and says so plainly when a whitelist is about to make the server invite-only. Both
      lists are written to `config.json` **only when you use them**
- [x] **Per-template change log** — every template keeps a searchable, tamper-proof history
      of what changed and when: mods added/removed and version locks, scenario changes, and
      each setting edit (old → new), timestamped in your server's timezone. Read-only — it
      can't be altered or deleted, and is removed only with the template
- [x] Multiple concurrent server instances (stable + experimental side by side), each a
      Docker container spawned and supervised by the manager
- [x] Live server logs in the browser (with a clear-window button), crash auto-restart
- [x] A **status bar in the top banner** shows every server at a glance — online state and
      player count — from any page, and the instance page flags when a template changed
      since the server started, with a one-click restart to apply it
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
                │
                │  Docker API, through a least-privilege socket proxy
                │  (the manager never mounts /var/run/docker.sock itself)
                ▼
             host Docker daemon
                ├─► steamcmd container (one-shot downloads → shared volumes)
                └─► reforger-instance-* containers (one per server)
```

The compose file holds the manager and the socket proxy — plus Caddy in the VPS setup.
Server instances and SteamCMD download jobs are sibling containers the manager creates
through the Docker API, attached to the same Docker network and labeled so they survive
manager restarts. The proxy sits on its own internal network, so the game containers
cannot reach the Docker API even though they share a network with the manager.

Server files are downloaded once per branch into local folders
(`./serverfiles/stable`, `./serverfiles/experimental`) and mounted read-only into each
instance.

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

## Installation

Three ways to run it. Pick the one that matches where it will live — each section is
self-contained and includes how to update it later.

| | Best for | You'll open |
|---|---|---|
| **[1. Linux at home](#1-linux-at-home-or-on-a-lan)** | A machine on your own network | `http://localhost:7780` |
| **[2. Linux on a VPS](#2-linux-on-a-public-vps-https)** | A rented server, reachable anywhere | `https://your-domain` |
| **[3. Windows](#3-windows-10-and-11)** | Docker Desktop on your PC | `http://localhost:7780` |

Players always connect over **UDP game ports**, opened separately from the web GUI —
each section says which.

> ### ⚠️ Treat the GUI login as host credentials
>
> Managing containers means talking to the Docker daemon, which is root-equivalent on the
> machine. The manager never mounts the Docker socket directly — a least-privilege proxy
> holds it, and every container the manager creates runs with `no-new-privileges` — but the
> daemon's inherent power remains. So use a strong password, never port-forward the GUI, and
> put TLS in front for remote access (scenario 2 does that for you).
>
> The app enforces the worst cases itself: bound anywhere other than `127.0.0.1`, it
> **refuses to start** on the example password, or with the login disabled unless you set
> `AUTH_DELEGATED_ACK=true` to confirm a reverse proxy authenticates every request. Behind
> TLS the session cookie is marked `Secure` automatically. A localhost-only bind stays
> frictionless — there these are warnings, not errors.
>
> Two things worth knowing:
> * **Behind a reverse proxy, set `TRUSTED_PROXIES`** to its address, or login rate-limiting
>   sees every visitor as the proxy. The VPS setup configures this for you.
> * **If you think a session leaked**, `POST /api/auth/logout-all` signs every device out
>   immediately — changing the password alone does not invalidate existing sessions.

### 1. Linux at home or on a LAN

```bash
curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-local.sh | sudo sh
```

That is the whole install. It checks for Docker (offering to install it), generates a
strong GUI password, offers to open the firewall, and starts everything. **It prints
your password at the end — save it.** Then open `http://localhost:7780`.

**Let players in:** forward UDP `2001-2020` and `17777-17796` on your **router** to this
machine, and give it a fixed LAN IP. Never forward the GUI port (`7780`) or the RCON
ports. Set `PUBLIC_ADDRESS` in `.env` to your public IP so servers advertise correctly.

**Updating:** `rsm update` — check the
[release notes](https://github.com/tubalainen/reforger-server-manager/releases) first;
anything needing a manual step is listed there under **Breaking changes**.

<details>
<summary><b>Prefer to set it up by hand?</b></summary>

You only need the `docker-compose.yaml` and a `.env` — not the source. Create a
folder, drop both in, edit `.env`, then pull and start:

```bash
mkdir reforger-server-manager && cd reforger-server-manager
curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/docker-compose.yaml
curl -fsSL  https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/.env.example -o .env
# edit .env: set ADMIN_PASSWORD and SESSION_SECRET
# (a '$' in a username/password must be doubled to '$$' — Compose eats a single '$')
docker compose pull
docker compose up -d
```

Open `http://localhost:7780` and sign in with the credentials from `.env`.
No other host setup is needed: the `data/` and `serverfiles/` folders are created on
first run, and the container fixes their ownership itself before dropping privileges
to an unprivileged user (uid 1000).

By default the GUI binds to `127.0.0.1` — put a reverse proxy (nginx/Caddy) with TLS in
front for VPS use, or set `WEB_BIND=0.0.0.0` at your own risk (login is still required).

Several people can use the GUI at once: lists refresh every few seconds, and editing a
server template locks it against the other sessions until that editor leaves (stuck
locks expire on their own, or use **Clear edit locks** on the Templates page). This
works over plain HTTP polling, so no extra nginx/Cloudflare-tunnel configuration is
needed — only the live server-log and download-progress views use WebSockets, which
both pass through by default.

**Updating a by-hand install** — refresh the compose file *before* pulling, or you keep
the old wiring:

```bash
cd /path/to/your/install
curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/docker-compose.yaml
docker compose pull
docker compose up -d --remove-orphans
```

Your `.env` is never touched. Upgrading restarts running servers; those with
**auto-start** come back on their own.

</details>

### 2. Linux on a public VPS (HTTPS)

**First:** point a domain at the server — an **A record** for e.g.
`reforger.example.com` → your VPS IP. Do this before installing, so the certificate
request succeeds first time.

```bash
curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-vps.sh | sudo sh
```

It asks for your domain, then puts **Caddy** in front with an automatic Let's Encrypt
certificate. The manager publishes no port of its own — it is reachable only through
Caddy — and the Docker API sits behind a least-privilege proxy. It prints your GUI
password at the end. Then open `https://your-domain`.

**Two firewalls to open, not one:**

| Where | What to allow |
|---|---|
| On the server (`ufw`) | The installer offers to do it, always allowing your current SSH port first |
| In your **VPS provider's panel** | TCP `80`, `443` and UDP `2001-2020`, `17777-17796` |

> Hetzner, AWS, Oracle, Google Cloud and DigitalOcean filter traffic *before* it reaches
> the machine, and several block almost everything by default. If your server never
> becomes reachable, this second firewall is nearly always why.

**Updating:** `rsm update` (check the
[release notes](https://github.com/tubalainen/reforger-server-manager/releases) first).

<details>
<summary><b>No domain yet? (testing only)</b></summary>

Set `SITE_ADDRESS=:80` and reach the GUI at `http://<your-vps-ip>`.

> **Testing only.** There is no certificate and therefore **no encryption**: your
> password and session cookie cross the internet in clear text, readable by anyone on
> the path. Use it to confirm the stack runs, then get a domain before real use. A
> domain costs a few euros a year and is the only way this is genuinely safe.

</details>

### 3. Windows 10 and 11

Windows is supported through **Docker Desktop with its WSL2 backend** — the same
manager image, the same Arma server containers, running inside the lightweight Linux
VM that Docker Desktop manages for you. You never have to touch WSL yourself.

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
.\start.ps1            # refresh the scripts + pull the newest manager image, then start
.\start.ps1 -NoUpdate  # start what's on disk, no script refresh and no pull (e.g. offline)
.\stop.ps1             # stop the manager (running Arma servers stay up)
.\stop.ps1 -All        # also stop every Arma server instance
```

Then do the [First run](#first-run) steps in the GUI (pull the runtime image, download
the server files) and create an instance.

#### Updating (and locking a version)

> **Check the [release notes](https://github.com/tubalainen/reforger-server-manager/releases)
> first.** A release that changes defaults or how the stack is wired says so under
> **Breaking changes** — including when re-running the installer is required, not optional.

**The manager updates itself on start.** Every time you launch it (the Desktop shortcut,
or `start.ps1`), it first refreshes these Windows helper scripts from GitHub — so a fix to
the scripts themselves reaches you automatically — and then pulls the newest manager image,
so you are always on the latest release. Your data, settings and running Arma servers are
untouched. To start without either — offline, or just faster — use `.\start.ps1 -NoUpdate`
(or `-NoSelfUpdate` to skip only the script refresh).

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

The helper scripts refresh themselves on every start, but the **compose file** and
`.env.example` do not. To update those (for instance when a release changes how the stack
is wired), re-run the installer — it refreshes every file in place and **keeps your existing
`.env`**. (If you're upgrading from a version whose `start.ps1` predates the self-update, run
the installer once to get the self-updating script; after that the scripts keep themselves
current.)

```powershell
$installer = "$env:TEMP\reforger-install.ps1"
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/install.ps1 -OutFile $installer
powershell -ExecutionPolicy Bypass -File $installer
```

The Arma **server runtime image** and the **server files** are updated separately from the
GUI, on the [First run](#first-run) / Downloads screens — pull the runtime image and
re-download the branch when you want those refreshed.

#### Uninstalling (or starting over after a failed install)

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

#### Where the files live

The Windows compose file keeps state in **Docker named volumes**
(`reforger-data`, `reforger-serverfiles-stable`, `reforger-serverfiles-experimental`),
not in a Windows folder. The ~10 GB of server files are Linux-owned and heavily read
during startup: on the VM's native ext4 disk that is fast and permission-clean, while
on an Explorer folder it would be slow (9p) and prone to ownership errors. Browse or
back them up from **Docker Desktop → Volumes**.

#### Letting players in

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

#### Keeping it running

- Server containers already carry a restart policy, so they come back with Docker.
- In **Docker Desktop → Settings → General**, tick **Start Docker Desktop when you sign
  in** so a reboot brings everything back by itself.
- Docker Desktop runs inside your *user session*, not as a service. On an unattended
  machine that means the box must actually sign in after a reboot — configure automatic
  sign-in (`netplwiz`, untick "Users must enter a user name and password") or expect to
  log in manually before the servers come back.

#### Do not run Docker CE inside WSL

Installing Docker Engine *inside* a WSL distro instead of using Docker Desktop looks
tempting, but its NAT only forwards TCP: the published **UDP** game/A2S ports never
reach the Windows host, and `netsh portproxy` cannot help (it is TCP-only). Players
would never see or join your server. Use Docker Desktop.

## First run

Under **Server Instances → Server files** (at the bottom of the Instances page), do both
one-time steps before creating a server instance:

1. **Pull the server runtime image** — the Docker image each instance runs from
   (`REFORGER_SERVER_IMAGE`). `docker compose up` only pulls the manager itself, so the
   runtime image must be fetched once here.
2. **Download the server files** for the branch you want (Stable / Experimental) — the
   ~10 GB of game data mounted into every instance of that branch.

Then head to **Instances** and create your first server from a template.

## Editing `config.json` by hand

The template wizard covers the settings most servers need, but it can't model every key:
`gameProperties` is **scenario-specific**, and mod authors and Bohemia both add keys of
their own. So the `config.json` panel on the right of the wizard has an **Edit JSON**
button that turns it into a full editor — syntax highlighting, line numbers, indent
guides, and errors marked as you type.

When you hit **Apply**, your edit is split in two:

- **Keys the wizard models** are read back into its fields. Change `maxPlayers` in the
  JSON and the Settings step's player limit moves with it.
- **Everything else is kept as a custom key** and re-applied over the config every time
  the template is rendered — so it survives later edits in the wizard, and lands in the
  `config.json` your servers actually run. The panel badges how many a template carries.

Validation is deliberately asymmetric:

| | Behaviour |
|---|---|
| Broken JSON | **Blocked** — marked inline, Apply disabled |
| Missing `game.scenarioId` | **Blocked** — the server would have no mission to load |
| A modelled key out of range (e.g. `maxPlayers: 9999`) | **Blocked** — the server would reject it |
| A key the GUI doesn't recognise | **Allowed**, listed as "kept as-is" |

That last row is the point: an unfamiliar key is usually you configuring your scenario,
not a typo — so it's flagged for visibility and never rejected.

> **Ports are the one exception.** `bindPort`, `publicPort`, `a2s.port` and `rcon.port`
> are assigned per instance from the manager's port ranges, so each server gets its own —
> a value you type for them here is kept in the template but overwritten when an instance
> runs. Set ports on the instance, not in the template's JSON.

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
