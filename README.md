# Reforger Server Manager

A web-based manager for **Arma Reforger Dedicated Servers** on Linux. Draw up server
templates (scenario, mods, settings) in an intuitive web GUI, download the server files
with SteamCMD at the click of a button, and run any number of server instances — each in
its own Docker container — from a single `docker-compose.yaml` on any VPS or home Linux box.

Inspired by (and a spiritual successor to) [Longbow / ArmaReforgerServerTool](https://github.com/soda3x/ArmaReforgerServerTool)
by soda3x — reimagined as a Linux-first, Dockerized web application.

> **Status: early scaffold (v0.1.0).** Login, app shell and the container plumbing are in
> place; the feature milestones below land one by one.

## Features (roadmap)

- [x] Single `docker-compose.yaml` + single `.env` deployment, built-in login
- [ ] One-click SteamCMD download of server files — **Stable** (app `1874900`) or
      **Experimental** (app `1890870`) — with live progress bars
- [ ] Server templates: pick a scenario straight from the
      [Workshop](https://reforger.armaplatform.com/workshop), auto-resolve all mod
      dependencies, add extra mods, tune settings, save — and download the resulting
      `config.json`
- [ ] Multiple concurrent server instances (stable + experimental side by side), each a
      Docker container spawned and supervised by the manager
- [ ] Live server logs in the browser, crash auto-restart, scheduled restarts

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
once per branch into shared volumes and mounted read-only into each instance.

## Quick start

```bash
git clone https://github.com/tubalainen/reforger-server-manager.git
cd reforger-server-manager
cp .env.example .env
# edit .env: set ADMIN_PASSWORD, SESSION_SECRET and DOCKER_GID (stat -c '%g' /var/run/docker.sock)
mkdir -p data && sudo chown -R 1000:1000 data
docker compose up -d
```

Open `http://localhost:8080` and sign in with the credentials from `.env`.

By default the GUI binds to `127.0.0.1` — put a reverse proxy (nginx/Caddy) with TLS in
front for VPS use, or set `WEB_BIND=0.0.0.0` at your own risk (login is still required).

> **Security note:** the manager mounts `/var/run/docker.sock`, which is root-equivalent
> on the host — that is what lets it create server containers. Treat the web GUI
> credentials accordingly and firewall the port.

## Development

```bash
# backend (Python 3.12+)
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests
uvicorn main:app --reload --port 8080

# frontend (Node 20+), proxies /api to :8080
cd frontend
npm install
npm run dev
```

Build the production image locally: comment `image:` / uncomment `build:` in
`docker-compose.yaml`, then `docker compose up --build -d`.

## Credits

- [soda3x/ArmaReforgerServerTool (Longbow)](https://github.com/soda3x/ArmaReforgerServerTool) — the original Windows GUI this project takes its feature set and inspiration from
- [acemod/docker-reforger](https://github.com/acemod/docker-reforger) and the wider community of Reforger container images
- [Bohemia Interactive — Arma Reforger server documentation](https://community.bistudio.com/wiki/Arma_Reforger:Server_Config)

## License

MIT
