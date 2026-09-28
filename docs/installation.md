# Installation and operations

This guide covers Linux, public VPS, and Windows installations, plus networking, security, updates, and removal.

[Back to README](../README.md) · [Feature tour](features.md) · [Releases](https://github.com/tubalainen/reforger-server-manager/releases)

## Choose a setup

| Setup | Best for | Manager address |
|---|---|---|
| [Linux at home or on a LAN](#linux-at-home-or-on-a-lan) | A machine on your own network | `http://localhost:7780` |
| [Linux on a public VPS](#linux-on-a-public-vps) | A rented server with a domain | `https://your-domain` |
| [Windows 10 or 11](#windows-10-or-11) | Docker Desktop on a PC | `http://localhost:7780` |
| [A second manager on the same machine](#several-installs-on-one-machine) | Another team, next to an install that already runs (Linux) | `http://localhost:7781` |

## Security

The manager can create and control containers, so its login should be treated like host credentials. Use a strong password, keep the GUI bound to localhost when possible, and put an authenticated HTTPS reverse proxy in front of any remote deployment. Never port-forward the GUI or RCON ports.

The supplied stack does not mount the Docker socket into the manager. Since v0.65.0 a small **Docker gate** holds it instead: its own container, with no network at all, that the manager reaches over a socket in a volume only the two of them share. The gate passes on only what the manager needs, and only for its own install:

- the manager sees and controls only the containers it created — another install's servers do not exist as far as it can tell;
- containers may only be created from the two images named in `.env`, with the install's name and label, never privileged or in the host's process or user namespaces;
- a container may mount only the install's own data and server-file volumes, never a host folder by its path. A server mounts just its own folder of the data volume as a *volume subpath*, which Docker opens with symbolic links refused, so a link planted in a folder cannot lead the mount anywhere else. This needs **Docker Engine 26 or newer** (Docker Desktop 4.29 or newer);
- exec, image builds, volumes, swarm, secrets, plugins and the rest of the Docker API are refused.

Manager-created containers also use `no-new-privileges`. The [security review](security-review.md) records every finding and what was done about it. Docker access is still powerful. Even a manager that has been taken over stays within its own containers, images and volumes, but it can still run code in its own game servers, and on Linux those share the host's network. These controls do not make a public, unencrypted GUI safe. If the GUI shows a yellow *"compose file is older than v0.65.0"* or a red *"game servers can reach the Docker API"* banner, follow [Updating setup files](#updating-setup-files).

The application also enforces safer exposed defaults:

- When bound beyond `127.0.0.1`, it refuses to start with the example password.
- Disabling built-in login on an exposed bind requires `AUTH_DELEGATED_ACK=true`, confirming that an upstream proxy authenticates every request.
- Session cookies are marked `Secure` behind HTTPS.
- Set `TRUSTED_PROXIES` when using a reverse proxy so rate limiting sees the real client address. The VPS installer does this automatically.
- If a session may have leaked, `POST /api/auth/logout-all` invalidates every active session. Changing the password alone does not invalidate existing sessions.

## Linux at home or on a LAN

```bash
curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-local.sh | sudo sh
```

The installer checks for Docker (Engine 26 or newer — it offers to install or upgrade it), generates a strong GUI password, optionally configures the firewall, and starts the stack. Save the password printed at the end, then open `http://localhost:7780`.

Update later with `rsm update`. Check the [release notes](https://github.com/tubalainen/reforger-server-manager/releases) first for breaking changes or manual steps.

### Manual Linux setup

Only `docker-compose.yaml` and `.env` are required, plus Docker Engine 26 or newer (`docker version` shows it). Create the `data` and `serverfiles` folders first: the compose file hands them to Docker as volumes and expects them to exist.

```bash
mkdir reforger-server-manager && cd reforger-server-manager
mkdir -p data serverfiles/stable serverfiles/experimental
curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/docker-compose.yaml
curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/.env.example -o .env
# Edit .env and set ADMIN_PASSWORD and SESSION_SECRET.
# Double any '$' in values as '$$' because Compose consumes a single '$'.
docker compose pull
docker compose up -d
```

The application fixes the ownership of `data/` on first run before dropping to its unprivileged user. The GUI binds to `127.0.0.1` by default. For remote use, place nginx or Caddy with TLS in front of it. Setting `WEB_BIND=0.0.0.0` exposes it directly and is not recommended.

To update a manual installation, refresh the compose file before pulling the images, then recreate the stack:

```bash
cd /path/to/your/install
curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/docker-compose.yaml
mkdir -p data serverfiles/stable serverfiles/experimental
docker compose pull
docker compose down
docker compose up -d --remove-orphans
```

`down` before `up` matters when the compose file changed a network: `up -d` alone keeps the existing network as it is. Your `.env` is not overwritten. After updating from a compose file older than v0.65.0, the old Docker API network is left behind unused; remove it once with `docker network rm reforger-docker-api`. An upgrade restarts active servers; instances with auto-start return automatically.

## Linux on a public VPS

Create a DNS A record such as `reforger.example.com` pointing to the VPS before installation, then run:

```bash
curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/linux/install-vps.sh | sudo sh
```

The installer places Caddy in front of the manager and obtains a Let's Encrypt certificate. The manager publishes no port of its own and is reachable only through Caddy. Save the generated password, then open your HTTPS domain.

This setup is demonstrated in the [video walkthrough](https://youtu.be/s6ml4SacnRo). Update later with `rsm update`, after checking the release notes.

### VPS without a domain

For a short test, set `SITE_ADDRESS=:80` and open `http://<your-vps-ip>`. This has no encryption: credentials and session cookies cross the network in clear text. Do not use it as a permanent setup.

## Windows 10 or 11

Windows uses Docker Desktop with its WSL2 backend. Open a normal PowerShell window and run:

```powershell
$installer = "$env:TEMP\reforger-install.ps1"
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/install.ps1 -OutFile $installer
powershell -ExecutionPolicy Bypass -File $installer
```

Optional arguments include `-InstallDir 'D:\Reforger'` and `-WebPort 8080`. Downloading the script before running it lets you inspect `%TEMP%\reforger-install.ps1`; avoid piping a remote script directly into `iex`.

The installer:

1. Installs WSL2 if required, with confirmation before the necessary restart.
2. Installs Docker Desktop if required and selects its WSL2 backend.
3. Creates `%USERPROFILE%\ReforgerServerManager` with compose, environment, start, stop, firewall, and uninstall files.
4. Generates the session secret and admin password.
5. Opens the Windows firewall for the default game and A2S UDP ranges.
6. Creates a **Reforger Server Manager** desktop shortcut.

Docker Desktop may show a sign-in screen on its first launch. Choose **Skip**; a Docker account is not required. The desktop shortcut subsequently starts Docker Desktop, starts the manager, and opens `http://localhost:7780`.

### Windows commands and updates

```powershell
cd $env:USERPROFILE\ReforgerServerManager
.\start.ps1            # Refresh helpers, pull the manager image, and start
.\start.ps1 -NoUpdate  # Start without refreshing or pulling
.\stop.ps1             # Stop the manager; leave Arma instances running
.\stop.ps1 -All        # Stop the manager and every Arma instance
```

The start script normally refreshes the Windows helper scripts and pulls the configured manager image. To pin a release, set `MANAGER_VERSION` in `.env` to a tag from the [Releases page](https://github.com/tubalainen/reforger-server-manager/releases):

```dotenv
MANAGER_VERSION=v0.31.0
```

Use `MANAGER_VERSION=latest` to follow current releases. Re-run the installer when release notes require updated compose wiring; it keeps your existing `.env`. The server runtime image and Arma server files are separate from manager updates and are managed from the GUI.

### Windows uninstall

```powershell
cd $env:USERPROFILE\ReforgerServerManager
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

If the install folder is missing, download the uninstaller first:

```powershell
$u = "$env:TEMP\reforger-uninstall.ps1"
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/scripts/windows/uninstall.ps1 -OutFile $u
powershell -ExecutionPolicy Bypass -File $u
```

The uninstaller lists what it found and requires typing `REMOVE`. By default it removes containers, installed helpers, credentials, shortcut, and firewall rule but keeps Docker volumes containing templates, instances, saves, and server files. `-RemoveData` deletes those volumes irreversibly; `-RemoveImages` also removes images. Docker Desktop and WSL2 are never removed.

### Windows storage and startup

Persistent data uses Docker named volumes: `reforger-data`, `reforger-serverfiles-stable`, and `reforger-serverfiles-experimental` (with `RSM_STACK` set in `.env`, they start with that name instead of `reforger`). View or back them up from **Docker Desktop → Volumes**.

Server containers have restart policies. Enable **Start Docker Desktop when you sign in** to recover them after a reboot. Docker Desktop runs in the user session, so an unattended machine must sign in before the engine can start.

Do not install Docker Engine inside a WSL distribution for this project. WSL NAT does not publish the required UDP game and A2S ports to the Windows host. Use Docker Desktop.

## Several installs on one machine

**To add another Reforger Server Manager next to one that already runs on the machine** — for a second team, say — set up a second *stack*. Each stack is a complete, independent install: its own manager, Docker gate, game servers, data, server files and GUI login. Every container, network and volume a stack creates starts with its name, `RSM_STACK` in `.env`. The first install keeps the default name, `reforger`.

Each manager sees and controls only its own stack. The other team's servers, networks and folders do not exist as far as it can tell, and it cannot start, stop or remove them.

This is Linux only for now, and it is done by hand: `rsm` and the installers look after the first install only. On Windows, and on a VPS with the Caddy setup (only one stack can hold ports 80 and 443), keep to one install per machine for now. Tooling for several stacks is planned in [#204](https://github.com/tubalainen/reforger-server-manager/issues/204).

### Before you start

- **The first install must run v0.65.0 or newer, with the v0.65.0 compose file.** An older manager does not know about stacks, so it would treat the second stack's servers as its own. Check: `docker ps` lists a `reforger-docker-gate` container, and the first GUI shows no yellow *"compose file is older than v0.65.0"* banner. If it does, update the first install first ([Updating setup files](#updating-setup-files)).
- Docker Engine 26 or newer (`docker version --format '{{.Server.Version}}'`).
- About 10 GB of disk per server branch: each stack downloads its own server files.

### 1. Create its folder and files

Pick a name for the stack: lower-case letters, digits and underscores, no dashes (`team2`, `milsim_eu`). The folder name is free.

```bash
sudo mkdir -p /opt/rsm-team2 && cd /opt/rsm-team2
sudo mkdir -p data serverfiles/stable serverfiles/experimental
sudo curl -fsSLO https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/docker-compose.yaml
sudo curl -fsSL https://raw.githubusercontent.com/tubalainen/reforger-server-manager/main/.env.example -o .env
```

### 2. Give it its own name, ports and password

Edit `.env` (`sudo nano .env`) and **change** these lines, which are already in the file, rather than adding new ones:

```dotenv
RSM_STACK=team2
WEB_PORT=7781
GAME_PORT_RANGE=2101-2120
A2S_PORT_RANGE=17877-17896
RCON_PORT_RANGE=20099-20118
ADMIN_PASSWORD=<a long, unique password>
SESSION_SECRET=<output of: openssl rand -hex 32>
```

Then add this line, so Docker Compose never mixes the two installs up:

```dotenv
COMPOSE_PROJECT_NAME=team2
```

No two stacks on the machine may share a `WEB_PORT` or overlap in any port range. The first install uses the defaults: `7780`, `2001-2020`, `17777-17796` and `19999-20018`. Copy any other setting you changed for the first install, such as `PUBLIC_ADDRESS` or `WEB_BIND`. As everywhere in `.env`, write a literal `$` in a password as `$$`.

### 3. Start it and open its ports

```bash
cd /opt/rsm-team2
sudo docker compose pull
sudo docker compose up -d
```

Open its game and A2S ranges in the firewall (and forward them on your router, as for the first install):

```bash
sudo ufw allow 2101:2120/udp
sudo ufw allow 17877:17896/udp
```

The second GUI is at `http://localhost:7781` (or the address its `WEB_BIND` allows), with user `admin` and the password from its `.env`. Give that login to the second team; they need nothing else.

### Running it day to day

`rsm` only drives the first install, so run the second one's commands from its own folder:

```bash
cd /opt/rsm-team2
sudo docker compose ps                                  # status
sudo docker compose logs -f --tail 200 manager          # manager log
sudo docker compose pull && sudo docker compose up -d   # update to the newest image
sudo docker compose down                                # stop it and its game servers
sudo docker compose up -d                               # start it again
```

When the release notes say the compose file changed, download it again into this folder (step 1's `curl` line), then run `docker compose down` and `docker compose up -d`.

To remove the stack and **everything it stored** (its templates, saves and server files):

```bash
cd /opt/rsm-team2 && sudo docker compose down
sudo docker volume rm team2-data team2-serverfiles-stable team2-serverfiles-experimental team2-docker-gate
cd / && sudo rm -rf /opt/rsm-team2
```

### Good to know

- The stacks share the machine's network. On Linux the game servers use host networking, so port ranges must not overlap, and a game server can reach whatever listens on the host, including the other stacks' GUI login pages on `localhost`. Keep every GUI password strong.
- Whoever administers the machine (shell or `sudo` access) still controls every stack; the separation is between the teams' GUIs.

## Networking and firewalls

| Purpose | Ports |
|---|---|
| Game | UDP `2001-2020` |
| A2S query | UDP `17777-17796` |
| RCON | `19999-20018` — do not expose publicly |
| Web GUI | TCP `7780` — do not expose publicly |

At home, allow the game and A2S ranges in the host firewall and forward them from the router to a fixed LAN address. Set `PUBLIC_ADDRESS` in `.env` to the public IP.

On a VPS, allow TCP `80` and `443` plus the game and A2S UDP ranges in both the host firewall and the provider firewall. Many providers filter traffic before it reaches the server.

On Windows, the installer creates the host firewall rule. To recreate it after changing ranges, run an elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\ReforgerServerManager\firewall.ps1" -GamePorts 2001-2020 -A2sPorts 17777-17796
```

The GUI shows the exact ports assigned to each instance under **Instances → Ports & firewall**.

## First run

After signing in:

1. Open **Downloads** and pull the server runtime image.
2. Download the stable or experimental Arma Reforger server files.
3. Create a server template.
4. Create and start an instance from that template.

Multiple users can use the GUI simultaneously. Lists refresh automatically, and template editing uses temporary locks to prevent conflicting changes. Live logs and download progress use WebSockets, which standard nginx and Caddy configurations pass through.

## AI mod ordering with Ollama (optional)

The template wizard's **Sort › Order with an AI…** always works by copy and paste. To answer it in one click without sending the mod list anywhere, run [Ollama](https://ollama.com) — the compose files carry it as an optional service with NVIDIA GPU access (on Linux this needs the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/); Docker Desktop on Windows needs nothing extra). Add to `.env`:

```bash
COMPOSE_PROFILES=ollama
AI_ORDER_URL=http://ollama:11434
AI_ORDER_MODEL=qwen2.5:3b
```

Then start the stack again and download the model once:

```bash
docker compose up -d
docker exec reforger-ollama ollama pull qwen2.5:3b
```

**Which model:** `qwen2.5:3b` (~1.9 GB) is the recommendation for a GPU with **4 GB** of memory and the default when `AI_ORDER_MODEL` is empty — it follows the answer format reliably and leaves room for the context a 100+ mod list needs. `llama3.2:3b` also fits. With 8 GB or more, `qwen2.5:7b` orders better. Avoid "thinking" models such as `qwen3` or `deepseek-r1` on a small GPU: they reason for minutes before answering.

No NVIDIA GPU? Delete the `deploy:` block of the `ollama` service and it runs on the CPU, slowly. Already running Ollama on the host? Skip the profile, start Ollama with `OLLAMA_HOST=0.0.0.0` (keep port 11434 firewalled from the internet) and set `AI_ORDER_URL=http://host.docker.internal:11434`. A reverse proxy in front of the manager must allow a request to take a few minutes (nginx's default `proxy_read_timeout` is 60s).

## Updating setup files

Pulling a new container image does not update `docker-compose.yaml`, `.env.example`, or local helper scripts. Check each release's **Breaking changes** section:

- Managed Linux installations: run `rsm update`. When it offers a new compose file, accept it and let it recreate the stack (or run `rsm restart` later) — a new compose file takes effect only then.
- Manual Linux installations: download the current compose file before `docker compose pull`, then `docker compose down` and `docker compose up -d`.
- Windows installations: the start script refreshes helpers; re-run the installer when the compose setup changes.

Installers and update helpers preserve `.env` unless their output explicitly says otherwise.
