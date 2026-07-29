# Session notes — security review and hardening (2026-07-27 → 2026-07-29)

Handoff for picking this work up later. The *what* is in the release notes and the
security review; this file records the **context that isn't in the repo** — decisions
and why, what is deliberately unfinished, and the traps found along the way.

**Where things ended:** `main` at `0f96897`, **v0.45.0 released**, working tree clean,
339 backend + 66 frontend tests passing.

---

## 1. What was delivered

A full security review of the project, then every finding fixed or explicitly accepted,
across two releases — plus one-line Linux installers, an HTTPS VPS deployment, and a
documentation restructure.

| Doc | What it is |
|---|---|
| [`security-review-2026-07-27.md`](security-review-2026-07-27.md) | The review. All 14 findings ranked, each with an update note recording what was done and what was not |
| [`release-notes/v0.44.0.md`](release-notes/v0.44.0.md) | R1–R4, R6, R7, R12 + installers + VPS setup |
| [`release-notes/v0.45.0.md`](release-notes/v0.45.0.md) | R5, R8, R10, R11, R13, R14 + R9 decision |

### New files

```
docker-compose.vps.yaml          Caddy + auto-HTTPS, manager publishes no port
scripts/linux/install-local.sh   one-line install, home/LAN
scripts/linux/install-vps.sh     one-line install, public VPS
scripts/linux/rsm.sh             installed as /usr/local/bin/rsm
```

### Commits (newest first)

```
0f96897  docs: README in line with v0.44/v0.45 hardening
8b87f63  Close remaining findings (R5,R8,R10,R11,R13,R14) — v0.45.0   (#149)
4d37641  docs: restructure installation around the three scenarios
7e22bfb  docs: lead with the question users actually have             (#148)
a62556e  ci: re-publish release notes when they drift                 (#147)
6df1831  docs: flag v0.44.0 breaking changes + upgrade paths          (#146)
18ca1a6  Security hardening R1–R4, R6, R7, R12 — v0.44.0              (#145)
```

---

## 2. Decisions worth remembering

These are judgment calls, not oversights. If someone later asks "why wasn't X pinned /
encrypted / locked down", this is the answer.

| Decision | Reasoning |
|---|---|
| **No image digest pinning** (R9) | The Arma image must track game updates. A digest pin leaves every install on a stale build after each Reforger release until manually bumped — trading a real recurring usability failure for a speculative supply-chain one. `.env.example` documents how to pin voluntarily |
| **No encryption at rest** (R5) | The key would live in `.env` on the same host as the database it protects, so it only defends against a DB-only leak — while adding a way to lose every template forever (rotate/lose `SESSION_SECRET`) and an ordering dependency between backups |
| **No `cap_drop: ALL`** (R8) | No Docker daemon in the dev environment to boot a real Arma server against. Dropping capabilities on an image with undocumented requirements would surface as *servers failing to start* in production. Shipped `no-new-privileges` only |
| **`auth_enabled` stays public** on `/api/version` (R6) | The SPA reads it before login to decide whether to render the login UI. Removing it breaks the GUI, and it leaks nothing an attacker can't learn by calling any protected endpoint |
| **Origin checks, not CSRF tokens** (R7) | Single shared account, no third-party embedding — same protection, far less machinery |
| **Reject metacharacters, not an allow-list** (R10) | A token allow-list breaks whenever Bohemia adds an engine flag, which is exactly what the `extra_args` escape hatch exists for |
| **7-day session TTL unchanged** (R13) | Shortening it logs every existing user out on upgrade, for modest gain now that `logout-all` gives instant revocation |
| **LAN bind (`0.0.0.0`) still refuses a weak password** | Discussed and deliberately kept. The app can't distinguish a NAT'd home LAN from a public VPS, and "set a password" is 30 seconds of work that also protects against everything else on the network |

---

## 3. Still open

Nothing is half-finished in the repo. These need a real machine or a change of scope.

### Needs a real host
- [ ] **Run the installers end-to-end on a throwaway VPS.** Every part is verified in
      isolation (shellcheck, `dash -n`, SSH-port detection, `/dev/tty` prompts, generated
      `.env` clearing the startup gate) but they have **never been run start to finish**.
      They install Docker and rewrite firewalls, so this could not be done in the dev
      environment.
- [ ] **`cap_drop: ALL` on game containers** — the last meaningful hardening step. Needs
      one real server boot to confirm nothing breaks. The change itself is small; the
      verification is the hard part.

### Scope changes, only if wanted
- [ ] Rootless Docker / userns-remap (host-level ops, can't be enforced from compose)
- [ ] Encryption at rest, if the database ever moves off the host holding `.env`
- [ ] Per-user accounts and MFA, if one shared admin stops being enough

### Small
- [ ] Socket proxy has no `reforger-manager.managed=true` label, so `uninstall.ps1`'s
      label sweep misses it. Harmless in practice — `compose down --remove-orphans` runs
      first and catches it
- [ ] **Screenshots / short video.** The five images in `docs/images/` may have drifted,
      and two documented features have no screenshot at all: **Mods Overview** and the
      **per-template change log**. README renders at `width="820"` — keep window widths
      consistent. **Scrub before publishing:** the settings step shows admin/RCON password
      fields and the instance page shows the real public IP

---

## 4. Environment limitations hit (these will recur)

Worth knowing before re-attempting anything here:

- **No Docker daemon** — the `docker` CLI exists and `docker compose config` works
  (pure parsing), but nothing can actually run. This is why R8 stopped at
  `no-new-privileges`.
- **No `gh` CLI, and direct GitHub API calls are blocked** — `api.github.com` returns
  `403 GitHub access is not enabled for this session`. All GitHub work went through the
  MCP server, which has no release-editing tool. That gap is why
  `.github/workflows/release.yml` now re-publishes release notes automatically.
- **Commits cannot be signed** — the signing key is a 0-byte file with no private half,
  so a stop hook flags every commit as Unverified. Identity is already correct
  (`Claude <noreply@anthropic.com>`); only a real key registered on the GitHub account
  fixes it.

---

## 5. Bugs found and fixed *during* the work

Recorded because each was caught by testing rather than review, and each would have
shipped silently:

1. **Socket proxy shared a network with the game containers.** The first cut of R1 put it
   on `reforger-net`, where the spawned servers live — giving mod code (running as root)
   a network-reachable Docker API. That escape path did not exist before the proxy. Fixed
   with a dedicated `internal` network.
2. **The VPS compose disarmed its own security gate.** It read `WEB_BIND` from `.env`,
   which resolves to `127.0.0.1`, so the startup gate treated an internet-facing
   deployment as local and would not have refused a weak password. Now hardcoded to
   `0.0.0.0` with a comment explaining why it must not be overridable.
3. **A README rewrite truncated a code block.** The heading parser matched `# edit .env:`
   *inside* a bash fence, cutting the manual-install steps and leaving an unbalanced
   fence. Reverted, made the parser fence-aware, added assertions.
4. **A link validator that was wrong, not the links.** It collapsed repeated spaces into
   one hyphen; GitHub maps each space separately, so `Windows 11 / 10` →
   `windows-11--10`. Nearly "fixed" four working links.

---

## 6. Quick reference

### VPS install layout
```
/opt/reforger-server-manager/docker-compose.vps.yaml
/opt/reforger-server-manager/.env                 mode 600, holds the generated password
/opt/reforger-server-manager/data/                DB, configs, saves  (mode 700)
/opt/reforger-server-manager/serverfiles/{stable,experimental}/
/etc/reforger-server-manager.conf                 tells rsm where the above is
/usr/local/bin/rsm
```
Note the compose file is **`docker-compose.vps.yaml`**, so a bare `docker compose ps`
finds nothing — use `rsm status` or `-f docker-compose.vps.yaml`.

```
rsm start | stop | restart | status | logs [svc] | update | config | uninstall
rsm logs caddy      # certificate issuance
```

### Security-relevant settings (all in `.env.example`)
`AUTH_DELEGATED_ACK` · `TRUSTED_PROXIES` · `API_DOCS` · `ALLOWED_ORIGINS` ·
`CONTENT_SECURITY_POLICY` · `SITE_ADDRESS`, `PROXY_SUBNET` (VPS only)

### Certificates / DNS
HTTP-01 + TLS-ALPN-01, so **DNS can be hosted anywhere** and no API credentials are
needed. Ports 80 *and* 443 must be reachable. The trap: **Let's Encrypt prefers IPv6**,
so an AAAA record on a host without working IPv6 fails validation however correct the A
record is — the installer now detects and says this.

### Release process
Push to `main` → `release.yml` reads `APP_VERSION` from `backend/config.py`, cuts the tag
and release from `docs/release-notes/v<version>.md`, and publishes semver-pinned images.
Editing a notes file afterwards re-publishes it to the existing release automatically.
**Backend and frontend versions must match** — CI fails on drift.
