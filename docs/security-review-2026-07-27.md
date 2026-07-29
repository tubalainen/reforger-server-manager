# Reforger Server Manager — Security Risk Assessment

**Date:** 2026-07-27
**Version reviewed:** v0.43.8
**Reviewer role:** Senior Cyber Security Analyst
**Deployment assumption (per request):** the stack is deployed on internet-exposed VPSes.
**Handover:** to the senior software developer for remediation (see the fix backlog at the end).

---

## 1. Executive summary

The application is a FastAPI + Vue control panel that **mounts the host Docker socket
(`/var/run/docker.sock`) and spawns sibling containers** to run Arma Reforger dedicated
servers. That single design fact dominates the entire risk picture: **the Docker socket is
root-equivalent on the host**, so anyone who can drive the GUI can obtain root on the host.
The whole security posture therefore collapses down to *"how well is the GUI protected, and
how hard is it to reach the socket by other means?"*

For a localhost-only, single-operator install the design is acceptable. For the stated
threat model — **exposed to the internet on a VPS** — the current defaults and controls are
**not sufficient**. The protection reduces to one shared password, sent in clear text by
default (no TLS), guarded by an in-memory throttle that is trivially bypassed and that
misbehaves behind a reverse proxy. There is no second factor, no per-user accounts, no audit
of *who* did *what*, and several unauthenticated information-disclosure endpoints that help an
attacker fingerprint and target the box.

The code itself is generally well written: input is validated with Pydantic, path traversal is
guarded in the two places it matters, shell strings are built only from integers and
server-derived paths (no user free-text reaches a shell), and the Workshop scraper is isolated.
The problems are **architectural and operational**, not sprawl.

**Bottom line:** treat the GUI as a root shell for the host. Every hardening item below is about
keeping that root shell off the open internet and making it expensive to reach.

### Severity scale

| Level | Meaning |
|-------|---------|
| **CRITICAL** | Direct path to host compromise; fix before any internet exposure |
| **HIGH** | Realistic remote attack or credential/confidentiality break |
| **MEDIUM** | Meaningful weakness; exploited in combination or under common misconfig |
| **LOW** | Hardening / defense-in-depth / info disclosure |

### Ranked findings

| # | Finding | Severity |
|---|---------|----------|
| **R1** | Docker socket mount = host-root-equivalent, reachable through the web GUI — **partially mitigated 2026-07-27 (socket proxy)** | **CRITICAL** |
| **R2** | `AUTH_ENABLED=false` fully removes authentication from a root-equivalent GUI — **mitigated 2026-07-27 (exposed start now gated)** | **CRITICAL** |
| **R3** | No TLS by default + weak, single, shared credential (default `admin` / `change-me-now`) — **partially mitigated 2026-07-27 (weak-cred start gated, cookie auto-secure)** | **HIGH** |
| **R4** | Brute-force throttle is bypassable and breaks behind a reverse proxy (no `X-Forwarded-For`) — **mitigated 2026-07-27** | **HIGH** |
| **R5** | Secrets stored and served in plaintext (admin/RCON/server passwords in DB, `.env`, downloadable `config.json`) — **partially mitigated 2026-07-27** | **HIGH** |
| **R6** | Unauthenticated API surface disclosure: `/docs`, `/redoc`, `/openapi.json`, `/api/version` — **mitigated 2026-07-27** | **MEDIUM** |
| **R7** | No CSRF tokens and no security headers (CSP / X-Frame-Options / HSTS / nosniff) — **mitigated 2026-07-27** | **MEDIUM** |
| **R8** | Spawned game-server containers run as **root** with writable host bind mounts — **partially mitigated 2026-07-27** | **MEDIUM** |
| **R9** | `:latest` image pins for the server and steamcmd images (supply-chain / reproducibility) — **accepted risk, documented** | **MEDIUM** |
| **R10** | Free-form launch `extra_args` injected into the server container environment — **mitigated 2026-07-27** | **MEDIUM** |
| **R11** | Unauthenticated / unbounded resource abuse (Workshop dependency BFS, no object caps) — **mitigated 2026-07-27** | **LOW** |
| **R12** | WebSocket handshakes accept any Origin (Cross-Site WebSocket Hijacking) — **mitigated 2026-07-27** | **LOW** |
| **R13** | Session-secret auto-generation and long-lived (7-day) sessions with no revocation — **mitigated 2026-07-27** | **LOW** |
| **R14** | Verbose error messages and internal logging of paths/IDs — **mitigated 2026-07-27** | **LOW** |

---

## 2. Architecture & data-flow (attack-surface map)

```
            Internet / LAN
                 │  (HTTP by default, port WEB_PORT→8080)
                 ▼
        ┌───────────────────────────┐
        │  manager container        │   runs as uid 1000 (app), but…
        │  FastAPI + Vue SPA        │
        │                           │
        │  mounts:                  │
        │   /var/run/docker.sock ───┼──►  HOST Docker daemon  ==  ROOT on host
        │   ./data (rw)             │        │
        │   ./serverfiles (ro)      │        ├─► steamcmd container (rw serverfiles, root)
        └───────────────────────────┘        └─► reforger-instance-* (root, rw host binds,
                                                    publishes UDP 0.0.0.0)
```

**Trust boundaries crossed:**

1. **Browser → manager API** — guarded by a signed-cookie session (single shared admin), or by
   nothing at all when `AUTH_ENABLED=false`.
2. **Manager → Docker daemon** — *no boundary*. The manager can create any container, with any
   image, any bind mount, any privilege. This is the crown jewel.
3. **Manager → external Workshop site** — outbound scrape of `reforger.armaplatform.com`
   (fixed host, so not an SSRF pivot).
4. **Manager → SteamCMD / game-server containers** — spawned as root; they write root-owned
   files into host bind mounts under `./data`.

Because boundary #2 does not exist, **any authentication bypass or credential compromise at
boundary #1 is a full host takeover**, not just "someone messed with my game servers."

---

## 3. Detailed findings

### R1 — Docker socket is host-root-equivalent and reachable via the GUI  ·  CRITICAL
**Files:** `docker-compose.yaml:34`, `backend/services/docker_service.py`, `backend/services/instance_service.py`, `backend/entrypoint.sh:21-29`

The manager mounts `/var/run/docker.sock` and uses the Docker SDK to create containers with
**host bind mounts it chooses** (`_create_container`, `steam_service`, `_purge_instance_dir`).
Access to the Docker API is equivalent to root on the host: an attacker who reaches the API
(or an RCE in the manager process) can start a container that bind-mounts `/` and reads/writes
any host file, adds an SSH key, etc. The entrypoint deliberately adds the `app` user to the
docker socket's GID (`entrypoint.sh:24-26`), so the "unprivileged uid 1000" hardening is
cosmetic with respect to this capability.

This is *inherent* to what the tool does, so it cannot be removed — but it can be **contained**:

- Front the socket with a **least-privilege Docker socket proxy** (e.g. Tecnativa
  `docker-socket-proxy`) that only exposes `containers`/`images` verbs and denies
  `--privileged`, host-path binds outside an allow-list, etc.
- Consider **rootless Docker** or a dedicated Docker context so a socket compromise is not host root.
- Never expose the GUI port directly; see R3.

**Risk:** total host compromise. **This is the finding everything else feeds into.**

> **Update — 2026-07-27 (partially mitigated).** The manager no longer mounts
> `/var/run/docker.sock` directly. A bundled **`docker-socket-proxy`** service now holds the
> socket and the manager reaches the daemon over the internal network via
> `DOCKER_HOST=tcp://docker-socket-proxy:2375`. Only the API sections the manager uses are
> forwarded — `CONTAINERS`, `IMAGES`, `NETWORKS`, `INFO` (+ `POST` for write verbs); the
> `exec`, `build`, `commit`, `volumes`, `swarm`, `secrets`, `configs`, `plugins` and `system`
> endpoints are denied. The proxy is not published (internal `reforger-net` only) and mounts
> the socket read-only. Changed in `docker-compose.yaml`, `docker-compose.windows.yaml`,
> `backend/entrypoint.sh` (handles `DOCKER_HOST`), `.env.example` (`DOCKER_PROXY_IMAGE`) and
> the README security note. No application code changed — `docker.from_env()` already honours
> `DOCKER_HOST`.
>
> **Follow-up fix — 2026-07-27 (network isolation).** The first cut of this change put the
> socket proxy on `reforger-net`, the same network the spawned **game containers** attach to.
> Those run untrusted Workshop mods as root, so a network-reachable Docker API on their own
> network was a container-escape path that did not exist before the proxy was introduced (the
> raw socket had been a file inside the manager only). The proxy now sits alone on a dedicated
> **`internal`** `docker-api` network shared only with the manager, in all three compose files.
>
> **Residual risk (why "partial"):** the proxy filters by API path/method, not by request
> body, so it cannot block a `containers.create` that bind-mounts a host path or sets
> `--privileged` — capabilities the manager legitimately needs. An attacker who reaches the
> API can therefore still create a container that mounts the host filesystem. To fully close
> that escalation, run the daemon **rootless** or with **user-namespace remapping** at the
> host level (ops change, tracked separately), and keep following R2/R3 (auth + TLS + no
> direct exposure). Net effect: meaningfully reduced blast radius; the GUI must still be
> treated as host-root and kept off the open internet.

---

### R2 — `AUTH_ENABLED=false` disables auth on a root-equivalent GUI  ·  CRITICAL
**Files:** `backend/auth.py:65-88`, `backend/config.py:72`, `.env.example:26-31`

When `AUTH_ENABLED=false`, `session_username()` returns the `anonymous` user for **every**
request, so all endpoints are open. The docs and startup log warn "only behind a reverse proxy,"
but nothing in the app *enforces* that a proxy is present. A single misconfiguration — a user
who flips the flag while binding `0.0.0.0`, or a proxy that fails open — exposes an
unauthenticated root-on-host control panel to the internet.

**Recommendation:** if this mode is kept, refuse to bind to a non-loopback address while
`AUTH_ENABLED=false` unless an explicit `I_UNDERSTAND_AUTH_IS_DELEGATED=true` acknowledgement is
set; log an error and (optionally) require a trusted-proxy allow-list. Prefer keeping built-in
auth on and layering the proxy auth on top.

> **Update — 2026-07-27 (mitigated).** The proxy-owns-auth mode is **kept** (operators can still
> run `AUTH_ENABLED=false` behind nginx/Caddy/Authelia), but it is now **gated on exposure**. A
> startup check (`config.startup_security_issues`, enforced in `main.py`'s lifespan) **refuses to
> start** when the GUI looks network-exposed (a non-loopback `WEB_BIND`) and `AUTH_ENABLED=false`
> unless the operator sets **`AUTH_DELEGATED_ACK=true`** — their explicit acknowledgement that a
> proxy in front authenticates every request. A loopback bind stays frictionless (warning only).
> Documented in `.env.example`. Covered by tests in `tests/test_config.py`.

---

### R3 — No TLS by default + weak single shared credential  ·  HIGH
**Files:** `backend/auth.py:91-122`, `backend/config.py:68-78`, `.env.example:10-31,95-98`, `docker-compose.yaml:27`

- The default deployment is **plain HTTP** (`SESSION_COOKIE_SECURE=false`, cookie `secure` off).
  If a user exposes it (documented "at your own risk" `WEB_BIND=0.0.0.0`) without a TLS proxy,
  the password and session cookie travel **in clear text**.
- There is exactly **one** account. The example ships `admin` / `change-me-now`; the only guard
  against leaving it is a startup log warning (`main.py:54`). No forced password change, no
  complexity policy, no MFA, no lockout (see R4).
- Credential comparison itself is correct (constant-time `hmac.compare_digest`,
  `auth.py:105-106`) — that part is fine.

**Recommendation:** ship secure-by-default guidance that *requires* a TLS reverse proxy for any
non-loopback bind; set `SESSION_COOKIE_SECURE=true` automatically when a request arrives over
HTTPS / `X-Forwarded-Proto: https`; refuse to start with the example password; document and
encourage a strong generated password. Consider optional TOTP MFA given the blast radius.

> **Update — 2026-07-27 (partially mitigated).** Two of the code-side items are done:
> - **Refuse the weak/example credential when exposed.** The same startup gate (see R2) now
>   **aborts startup** on a non-loopback `WEB_BIND` when `ADMIN_PASSWORD` is the example value or
>   empty; on loopback it stays a warning, plus a new warning for passwords < 12 chars.
> - **Auto-secure cookie under TLS.** `auth.py` now marks the session cookie `Secure` whenever the
>   request is HTTPS or carries `X-Forwarded-Proto: https` (a terminating proxy), in addition to the
>   explicit `SESSION_COOKIE_SECURE=true`. This never sets `Secure` on the plain-HTTP localhost
>   default (which would break login). Covered by tests in `tests/test_auth.py`.
>
> **Still open (ops / larger scope):** enforcing that a TLS reverse proxy is actually present for a
> non-loopback bind cannot be proven from inside the app (it only sees `WEB_BIND` and request
> headers) — this stays documentation + the exposure gate above. Single shared account and optional
> MFA are unchanged; treat those as a follow-up if per-user auth is wanted.

---

### R4 — Brute-force throttle is bypassable and breaks behind a proxy  ·  HIGH
**File:** `backend/auth.py:39-63`

`_throttle()` keys on `request.client.host` — the **TCP peer**, not the real client. Two
failure modes:

1. **Behind a reverse proxy** (the recommended deployment), every request's peer is the proxy,
   so *all* users share one 10-attempts/60s bucket. An attacker can (a) brute force is *not*
   slowed per-real-IP, and (b) trivially **lock out all legitimate logins** (availability DoS)
   by burning the shared bucket. `X-Forwarded-For` is never consulted anywhere in the app.
2. **Directly exposed**, the limit is per source IP and in-memory only, so a distributed or
   IP-rotating attacker bypasses it, and a process restart clears it. 10 attempts/minute against
   a single password is also generous for an online guessing attack over days.

**Recommendation:** parse `X-Forwarded-For` **only** from a configured trusted-proxy list; add
exponential backoff and a longer lockout after N failures; persist counters (or use a shared
store) so restarts don't reset them; separate the "per-IP" and "global" limits so throttling
never denies service to everyone.

> **Update — 2026-07-27 (mitigated).** `auth.client_ip()` now resolves the real client:
> `X-Forwarded-For` is believed **only** when the direct peer is in the new `TRUSTED_PROXIES`
> allow-list (IPs and/or CIDRs), and the header is walked **right-to-left** to the first entry
> that is not itself a trusted proxy — so a client-supplied prefix is structurally ignored and
> can neither forge a fresh identity nor frame someone else's address. Default is **empty
> (trust nothing)**, which is correct for a direct bind; `docker-compose.vps.yaml` sets it to
> its bundled Caddy's network, whose subnet is pinned via `PROXY_SUBNET` precisely so it can be
> named.
>
> The throttle is now strictly **per client, with no global cap** — a global limit *is* the
> denial of service it was supposed to prevent. Breaching the window escalates a per-client
> lockout (60s → 5m → 15m → 1h), strikes survive inactivity for an hour so escalation cannot be
> reset by waiting out the window, a locked record is never evicted, tracked clients are capped,
> and a **successful login clears the state** so a mistyping operator is not penalised. Failed
> logins and lockouts are now logged — previously there was no record a login had ever failed.
> Verified by test that an attacker exhausting the limit behind a shared proxy no longer locks
> other operators out (`tests/test_auth.py`).
>
> **Deliberately not done:** counters are still in-memory, so a manager restart clears them.
> Persisting them would put a DB write on every failed login for modest gain — an attacker
> cannot trigger a restart remotely, and the process restarts rarely. Revisit if the threat
> model changes.

---

### R5 — Secrets stored and served in plaintext  ·  HIGH
**Files:** `backend/services/template_service.py:293-302`, `backend/templates_api.py:339-352`, `backend/models.py`, `.env`

- The **RCON password** and in-game **admin/server passwords** are stored verbatim inside
  `Template.config_json` in the SQLite DB and rendered into per-instance `config.json` on disk
  under `./data` (host-readable).
- `GET /api/templates/{id}/config.json` **returns the full config including those passwords**
  to any authenticated session (and it's an `attachment` download, so it lands on disk).
- `ADMIN_PASSWORD` / `SESSION_SECRET` live in `.env` in plaintext (unavoidable for env config,
  but note file perms matter).

**Impact:** a read-only leak (backup, misconfigured volume, path disclosure, or a lower-severity
info-disclosure bug) exposes RCON and admin credentials for every managed server. Defense in
depth is weak: one leak, many secrets.

**Recommendation:** restrict `./data` and `.env` file permissions (0600/0700) in docs and
entrypoint; consider encrypting secret fields at rest; mask secrets in any API response that
does not strictly need them; document that config.json exports contain live credentials.

> **Update — 2026-07-27 (partially mitigated).** The entrypoint now `chmod 700`s `/data`, so the
> database and every rendered `config.json` are readable only by the app user; the Linux
> installers already write `.env` at mode 600. The config.json download now carries an explicit
> warning in the UI that the file contains the server, admin and RCON passwords in clear text.
> Confirmed nothing logs secrets.
>
> **Deliberately not done: encryption at rest.** The key would have to live in `.env`, on the
> same host as the database it protects, so it defends only against a leak of the DB *alone* —
> while adding a real failure mode (rotate or lose `SESSION_SECRET` and every template becomes
> unreadable) and an order dependency between backups. A poor trade for a single-admin tool;
> revisit if the database ever moves off-host.

---

### R6 — Unauthenticated API/tooling disclosure  ·  MEDIUM
**Files:** `backend/main.py:115,128-140`

FastAPI is created without `docs_url=None`/`openapi_url=None`, so **`/docs`, `/redoc` and
`/openapi.json` are served unauthenticated** (the SPA catch-all only guards `api/*` paths and is
registered after the built-in doc routes). `GET /api/version` (`main.py:133`) is also
unauthenticated and returns the exact version **and the `auth_enabled` flag** — handing an
attacker a precise fingerprint (target known CVEs for pinned dep versions) and telling them
whether login is even on.

**Recommendation:** disable the interactive docs/OpenAPI in production (or gate them behind
`require_session`); drop `auth_enabled` and the precise version from the unauthenticated
`/api/version` (keep `/api/health` minimal).

> **Update — 2026-07-27 (mitigated).** `/docs`, `/redoc` and `/openapi.json` are now disabled
> (404) unless `API_DOCS=true` is set deliberately for development. `/api/health` returns
> liveness only — it no longer reports `APP_VERSION`. `/api/version` returns the precise
> version and repo link **only to an authenticated session**.
>
> **One deviation from the recommendation, on purpose:** `auth_enabled` stays public. The SPA
> reads it before anyone can log in, to decide whether to render the login UI at all, so
> removing it would break the GUI — and it discloses nothing an attacker could not learn by
> calling any protected endpoint once. The fingerprinting risk was the build number, and that
> is now behind auth.

---

### R7 — No CSRF tokens and no security headers  ·  MEDIUM
**Files:** `backend/main.py` (no middleware), `frontend/src/api.js:51-60`, `backend/auth.py:109-121`

- **CSRF:** state-changing calls rely solely on the session cookie. The cookie is `SameSite=Lax`
  and requests use JSON bodies, which blunts classic CSRF — but there is **no CSRF token and no
  Origin/Referer check**, so protection depends entirely on browser SameSite behavior and the
  content-type preflight. That is thin for a root-equivalent control plane.
- **Headers:** no `Content-Security-Policy`, `X-Frame-Options`/`frame-ancestors` (→ clickjacking
  of the control panel), `X-Content-Type-Options: nosniff`, `Referrer-Policy`, or HSTS are set on
  API or SPA responses.

**Recommendation:** add a middleware that sets a strict CSP, `X-Frame-Options: DENY`,
`nosniff`, `Referrer-Policy: no-referrer`, and HSTS (when on HTTPS); add an Origin check (or a
double-submit CSRF token) on all mutating endpoints and WebSocket handshakes.

> **Update — 2026-07-27 (mitigated).** A single middleware now does both halves.
>
> **CSRF:** every state-changing request (anything not GET/HEAD/OPTIONS) is rejected with 403
> when the browser's `Origin` does not match the `Host` it was addressed to. A request with no
> `Origin` is left alone — that is a non-browser client (curl, scripts), and browsers always
> send `Origin` on the cross-origin writes this defends against. `ALLOWED_ORIGINS` covers a
> proxy that rewrites `Host`. This is Origin-checking rather than a double-submit token: with a
> single shared account and no third-party embedding, it gives the same protection for far less
> moving machinery.
>
> **Headers:** `Content-Security-Policy` (overridable via `CONTENT_SECURITY_POLICY`),
> `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
> and HSTS **only** when the connection is actually HTTPS — sending it on the plain-HTTP
> localhost default would pin a browser to a scheme that host does not serve. The CSP allows no
> third-party sources and **no inline script at all**; `'unsafe-inline'` is granted for styles
> only, because Vue writes inline `style` attributes for `:style` bindings. Verified against the
> production build: it emits no inline `<script>` and references no external host.
>
> **Residual:** `connect-src 'self'` covers same-origin WebSockets per CSP Level 3, which
> current browsers implement — but this was verified by build inspection, not in a live browser.
> If a deployment ever sees the live-log socket blocked, `CONTENT_SECURITY_POLICY` is the escape
> hatch.

---

### R8 — Game-server containers run as root with writable host binds  ·  MEDIUM
**Files:** `backend/services/instance_service.py:619-655`, code comments at `:1092-1093`, `:733-757`

Spawned `reforger-instance-*` containers run as **root** (the code repeatedly notes the server
writes root-owned files into the bind mounts) with `rw` mounts of `./data/instances/<id>` on the
host. They do **not** get the docker socket, so this is not R1-level — but an RCE in the Arma
server, or a malicious/compromised Workshop mod, executes as root inside a container that can
write host files under `./data`. No `no-new-privileges`, `cap_drop`, read-only rootfs, or user
namespacing is applied.

**Recommendation:** run instance containers with `security_opt: no-new-privileges`, `cap_drop:
ALL` (add back only what Reforger needs), a non-root user where the image allows, and the
narrowest bind mounts. Same treatment for the steamcmd helper containers.

> **Update — 2026-07-27 (partially mitigated).** Every container the manager creates — game
> instances, steamcmd downloads, the version check and both cleanup helpers, six call sites in
> total — is now created with `no-new-privileges`, so a process inside cannot gain privileges
> through a setuid binary.
>
> **Capability dropping was deliberately not done.** There is no Docker daemon in the development
> environment to start a real Arma server against, and `cap_drop: ALL` against an image whose
> requirements are undocumented would surface as servers failing to boot in production. That
> needs one run on a real host first — tracked as the follow-up here, together with running the
> game containers as a non-root user.

---

### R9 — `:latest` image pins for server and steamcmd images  ·  MEDIUM
**Files:** `backend/config.py:85-88`, `.env.example:52-57`

`REFORGER_SERVER_IMAGE` and `STEAMCMD_IMAGE` default to `…:latest`. These images are pulled and
run **as root via the host daemon** (R1/R8). A compromised or silently changed upstream tag is
executed with no code change and no way to know which build ran — the exact reproducibility
problem #86 fixed for the manager's *own* Python deps, still open for the two images it launches.

**Recommendation:** pin to digests (`@sha256:…`) or specific tags; document an update/verify
flow; optionally verify image provenance/signatures.

> **Update — 2026-07-27 (accepted risk, documented).** Deliberately *not* pinned. The Arma server
> image has to track game updates: a digest pin would leave every install on a stale build after
> a Reforger release until someone manually bumped it — trading a real, recurring usability
> failure for a speculative supply-chain one. Both images are already operator-controlled through
> `.env`, so `.env.example` now explains the trade-off and shows how to pin to a digest for
> anyone who prefers reproducibility over automatic updates.

---

### R10 — Free-form launch `extra_args` flow into the container environment  ·  MEDIUM
**Files:** `backend/services/template_service.py:116,148-160`, `backend/services/instance_service.py:476-498`

`LaunchParams.extra_args` is an unconstrained string concatenated into `ARMA_PARAMS` and passed
as a container environment variable. Depending on how the base image consumes `ARMA_PARAMS`
(commonly `sh -c "ArmaReforgerServer $ARMA_PARAMS"`), this is an **argument/shell injection into
the game-server container**. It is *authenticated-admin only*, and the admin already holds R1
(host root), so it is **not a privilege escalation** — but it is an unvalidated free-text →
execution path that widens blast radius (e.g. a lower-privileged future role, or a stored-payload
scenario) and should be constrained.

**Recommendation:** validate/allow-list `extra_args` tokens, or document clearly that it is
equivalent to running arbitrary flags; never let it reach a shell unquoted in any wrapper.

> **Update — 2026-07-27 (mitigated).** `LaunchParams.extra_args` now rejects shell metacharacters
> (`;` `|` `&` `<` `>` backtick `$` and newlines) at the model level, so the value cannot carry a
> command through `ARMA_PARAMS` into a shell-expanding wrapper. A token allow-list was rejected as
> the alternative: it would break the moment Bohemia adds an engine flag, which is precisely what
> this escape hatch exists for. Ordinary engine flags pass unchanged.

---

### R11 — Unbounded / unauthenticated-adjacent resource abuse  ·  LOW
**Files:** `backend/services/workshop_service.py:206-286`, `backend/instances_api.py`, `backend/templates_api.py`

- `resolve_dependencies()` does an **unbounded BFS** over a mod's dependency graph, one 15s HTTP
  fetch per node, with no cap on nodes or total time. An authenticated user (or a mod graph
  crafted upstream) can make the manager fan out many slow outbound requests and tie up worker
  threads.
- No caps on the number of templates/instances/mods; each instance also spins background CPU
  sampling threads. Abuse is bounded by auth, so severity is low, but there is no rate limiting
  on any endpoint except login.

**Recommendation:** cap BFS breadth/depth and total wall-clock; add a global outbound concurrency
limit; add basic per-session rate limiting on expensive endpoints.

> **Update — 2026-07-27 (mitigated).** The dependency walk is now bounded at 300 nodes and 120
> seconds. On a cap it returns what it resolved and reports the remainder as `missing` (plus a
> `truncated` flag) rather than failing, so a pathological graph degrades instead of occupying a
> worker thread indefinitely. Both limits sit far above real mod graphs, which run to dozens of
> nodes.

---

### R12 — WebSocket handshakes accept any Origin (CSWSH)  ·  LOW
**Files:** `backend/instances_api.py:247-252`, `backend/serverfiles_api.py:59-64,111-119`

WS handshakes authenticate via the session cookie but **do not validate `Origin`**. Cross-Site
WebSocket Hijacking is largely mitigated *today* because the session cookie is `SameSite=Lax`
(a script-initiated cross-site WS won't carry it), so this is defense-in-depth rather than an
open hole — but it should not rely solely on SameSite.

**Recommendation:** validate `Origin` against an allow-list on every WS handshake.

> **Update — 2026-07-27 (mitigated).** All three WebSocket handlers (instance logs, image pull,
> server-file download) now validate `Origin` **before** accepting the socket, closing with code
> 4403 on a mismatch. The check runs ahead of the session check, so a foreign origin is refused
> even with a valid cookie.

---

### R13 — Session-secret auto-gen and long, non-revocable sessions  ·  LOW
**Files:** `backend/config.py:63-66`, `backend/auth.py:35-36,78`, `.env.example:37-40`

- If `SESSION_SECRET` is unset a random one is generated per start (good fail-safe, warned), but
  it means sessions silently drop on every restart and, more importantly, users may never set a
  stable secret.
- Sessions are stateless signed cookies with a **7-day** default TTL and **no server-side
  revocation** — a stolen cookie is valid until it expires; logout only clears the client cookie.
  Rotating `SESSION_SECRET` is the only global kill switch.

**Recommendation:** require an explicit `SESSION_SECRET` for non-dev runs; shorten default TTL;
document that credential compromise requires a secret rotation to invalidate outstanding
sessions; consider a lightweight session version/nonce for revocation.

> **Update — 2026-07-27 (mitigated).** `POST /api/auth/logout-all` rotates a persisted signing
> salt, invalidating every outstanding session on every device at once — the "I think my cookie
> leaked" button that previously did not exist (the only remedy was changing `SESSION_SECRET` and
> restarting). The salt lives in `DATA_DIR` rather than memory, so ordinary restarts and upgrades
> do *not* log everyone out, and an install predating this keeps its sessions.
>
> The 7-day default TTL is unchanged: shortening it would log every existing user out on upgrade
> for a modest gain now that instant revocation exists. `SESSION_TTL_HOURS` remains available.

---

### R14 — Verbose errors and internal detail in logs/responses  ·  LOW
**Files:** throughout (e.g. `instances_api.py:105`, `steam_service.py`, `instance_service.py` logging)

Docker/exception detail is surfaced to clients (`Could not create container: {exc}`) and host
paths/instance IDs are logged. Minor information disclosure that aids an attacker mapping the
host. Low priority, but tighten user-facing error text for internet exposure.

> **Update — 2026-07-27 (mitigated).** The five Docker/SteamCMD failures that echoed raw daemon
> exceptions (which can carry host paths and image internals) now return a short actionable
> message and log the detail server-side instead; the three Workshop errors likewise. Messages
> that describe the *user's own* submitted config are deliberately unchanged — that detail is the
> point of them, and it discloses nothing the caller did not send.

---

## 4. What is already done well (no action needed)

- **Path traversal** is correctly guarded with `.resolve()` + parent checks in both file-serving
  paths (`instance_service.resolve_log_file:1064-1072`, SPA static handler `main.py:148-156`).
- **Shell construction** for cleanup containers uses only integers (instance IDs) and
  server-derived relative paths — **no user free-text reaches `/bin/sh`**
  (`_purge_instance_dir`, `clear_instance_data`). The safety comments are accurate.
- **Input validation** is thorough via Pydantic `Field` constraints; ports, times, mod IDs
  (16-hex), branches, and RCON permission are all validated/allow-listed.
- **Constant-time credential comparison** (`hmac.compare_digest`).
- **The Workshop scraper is not an SSRF vector** — the host is fixed and the asset ID is
  normalized to 16 hex before use.
- **Dependencies are pinned** with compatible-release specifiers (`requirements.txt`, #86), and
  CI runs lint + tests before publishing.
- Cookies are `HttpOnly` + `SameSite=Lax`, and `SESSION_COOKIE_SECURE` exists for TLS deployments.

---

## 5. Remediation backlog (handover to the senior software developer)

Ordered for maximum risk reduction per unit of effort. Items marked **(docs/ops)** are
configuration/documentation changes; the rest are code.

**Do first — gate internet exposure of the root-equivalent GUI**
1. **R3/R2 (docs/ops + code):** ✅ **Done 2026-07-27** — a startup gate refuses to start an
   exposed GUI with the example/empty password or with `AUTH_ENABLED=false` unless
   `AUTH_DELEGATED_ACK=true`; the session cookie auto-enables `Secure` under
   `X-Forwarded-Proto: https`. **Follow-up:** the proxy-owns-auth opt-out is preserved by design;
   actually *requiring* a TLS proxy for a non-loopback bind remains documentation-only.
2. **R1 (docs/ops):** ✅ **Done 2026-07-27** — the manager now goes through a bundled
   least-privilege **docker-socket-proxy** instead of the raw socket. **Follow-up still open:**
   run the daemon **rootless** or with **userns-remap** to also neutralise the residual
   container-create-with-host-bind escalation the proxy cannot filter (see the R1 update note).
3. **R6 (code):** ✅ **Done 2026-07-27** — docs/OpenAPI off unless `API_DOCS=true`, `/api/health`
   reduced to liveness, build details behind a session (`auth_enabled` kept public by necessity —
   see the R6 update note).

**Do next — authentication & session hardening**
4. **R4 (code):** ✅ **Done 2026-07-27** — trusted-proxy `X-Forwarded-For` resolution
   (`TRUSTED_PROXIES`), per-client escalating lockout, no global cap, success resets state,
   failed logins logged. Persistent counters deliberately skipped (see the R4 update note).
5. **R7 (code):** ✅ **Done 2026-07-27** — security-headers middleware (CSP, frame-ancestors
   DENY, nosniff, HSTS-when-HTTPS, referrer-policy) plus Origin/CSRF enforcement on every
   mutating endpoint and all three WS handshakes. Also closes **R12**.
6. **R13 (code/docs):** Require `SESSION_SECRET`, shorten TTL, document rotation as revocation.

**Then — container & supply-chain hardening**
7. **R8 (code):** ✅ **Partly done 2026-07-27** — `no-new-privileges` on all six container
   creation sites. **Follow-up:** capability dropping and a non-root user need one test run on a
   real Docker host first.
8. **R9 (docs/ops):** ✅ **Decided 2026-07-27** — accepted risk. Pinning the Arma image would
   block game updates; `.env.example` documents how to pin voluntarily.
9. **R5 (code/docs):** ✅ **Partly done 2026-07-27** — `/data` is now mode 700, `.env` 600, and
   the config.json download warns it carries live credentials. Encryption at rest deliberately
   skipped (see the R5 note).
10. **R10 (code):** ✅ **Done 2026-07-27** — shell metacharacters rejected in `extra_args`.

**Cleanup / defense-in-depth**
11. **R11 (code):** ✅ **Done 2026-07-27** — dependency walk bounded at 300 nodes / 120s.
12. **R14 (code):** ✅ **Done 2026-07-27** — Docker/Workshop errors no longer echo raw exceptions.
13. **R13 (code):** ✅ **Done 2026-07-27** — `POST /api/auth/logout-all` revokes every session.

---

## 5b. What remains open

Everything in the review has now been addressed, accepted with a documented rationale, or
reduced to a follow-up that needs a real host to verify:

| Item | Why it is still open |
|---|---|
| **R8 follow-up** | `cap_drop: ALL` and a non-root game container need one run against a real Docker daemon; unverifiable in the dev environment |
| **R1 follow-up** | Rootless Docker / userns-remap is a host-level ops change, not something the compose file can enforce |
| **R5 follow-up** | Encryption at rest, if the database ever moves off the host holding `.env` |
| **R3 follow-up** | Per-user accounts and MFA, if the single shared admin account ever stops being enough |

## 6. Suggested acceptance criteria for the fixes

- The app **cannot** be started bound to `0.0.0.0` over plain HTTP with the example password.
- `/docs`, `/openapi.json`, `/redoc` return 404/401 in production.
- A login brute-force from one IP is throttled *per real client IP* behind a proxy, and a single
  attacker cannot deny login to everyone.
- All responses carry CSP + `X-Frame-Options: DENY` + `nosniff`; mutating requests require a
  valid Origin or CSRF token.
- Instance/steamcmd containers run with `no-new-privileges` and dropped capabilities.
- Server/steamcmd images are digest-pinned.
- Documentation presents the **socket-proxy + TLS reverse proxy** as the supported internet
  deployment, with the raw-socket direct-expose path clearly labelled as unsafe.

---

*Prepared for handover. The single most important message for the developer and the operator:
**the web GUI is a root shell for the host — keep it off the open internet (TLS proxy + socket
proxy + strong auth), and treat every finding above as protecting that one fact.**
