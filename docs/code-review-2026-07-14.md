# Reforger Server Manager — architecture & code review

**Date:** 2026-07-14 · **Version reviewed:** v0.29.1 (`cfb42b2`)
**Scope:** whole repository — backend (3,968 LOC), frontend (3,738 LOC), tests (2,036 LOC), Docker/CI/scripts.

---

## Executive summary

The codebase is in good shape for its age: layering is clean (API → service → Docker/DB), the Workshop scraper is properly isolated behind pure, fixture-tested functions, and there is **no orphaned code** — a mechanical sweep for unreferenced functions, classes, constants and exports across both backend and frontend returned nothing. Backend test coverage is genuine (174 tests, including the awkward cases: stale container logs, port drift, dependency graphs).

The problems are not sprawl; they are **five real bugs**, one **hot-path performance flaw**, and a **testing blind spot on the entire frontend** that has already let two bugs reach production.

Two themes run through the findings:

1. **The same bug keeps recurring in the log parser** because the regexes are unanchored. `maxFPS: 60` being mistaken for a live FPS reading was fixed in v0.27.1; the *identical* bug in the player-count regex is still live (**C1**).
2. **Failure is silent.** A Docker hiccup at boot permanently disables background recovery (**C2**); a failed `docker info` poisons OS detection for the process lifetime (**C3**); a failed API call makes a whole GUI panel *vanish* rather than show an error (**C4**). Each degrades the product without ever telling anyone.

Severity: **P1** = fix now (wrong behaviour users will hit) · **P2** = fix soon (design/perf debt with real cost) · **P3** = cleanup.

| # | Finding | Sev |
|---|---|---|
| C1 | `maxPlayers: 64` in a log line is read as *64 players online* | P1 |
| C2 | Background monitor never starts if Docker is briefly unreachable at boot | P1 |
| C3 | `docker info` failure is cached forever → wrong firewall command shown | P1 |
| C4 | A failed API call makes the "Stored data" panel disappear entirely | P1 |
| C5 | FastAPI 422 errors render as `[object Object]` | P2 |
| A1 | Docker API call amplification on the 5-second poll | P2 |
| A2 | `container.stats(stream=False)` blocks a worker ~1–2 s per poll | P2 |
| A3 | God modules: `instance_service.py` (1,433) and `TemplateWizard.vue` (1,410) | P2 |
| A4 | Duplicated job/progress/WebSocket machinery in two services | P3 |
| D1 | `Instance.container_id` is write-only dead state | P3 |
| D2 | API fields computed but never consumed | P3 |
| D3 | Two in-memory dicts grow without eviction | P3 |
| P1 | **No frontend tests at all** — and no runner configured | P2 |
| P2 | No linting in CI | P3 |
| P3 | Unpinned Python dependencies | P3 |
| S1 | Session cookie has no `secure` flag | P2 |

---

## C1 — `maxPlayers: 64` is parsed as 64 players online — **P1**

**Where:** `backend/services/instance_service.py:43`

```python
_PLAYERS_STAT_RE = re.compile(r"Players?:\s*(\d+)")   # unanchored
```

The pattern matches *anywhere* in a line, so any log line echoing configuration — `maxPlayers: 64` — is read as a live player count. Reproduced against the current code:

```
line: "  ENGINE : maxPlayers: 64, maxFPS: 60"
parse_server_status(line) -> {'fps': None, 'mem_kb': None, 'players': 64}
```

This is the **same defect** that was found and fixed for FPS in v0.27.1, where `_FPS_RE` gained a `(?<![A-Za-z])` guard. The player regex on the very next line was left alone. The bad value propagates into the instance status bar and into `players_total` on the summary bar (so the dashboard can claim players on an empty server).

**Fix:** anchor it the same way, and audit the neighbours (`_MEM_RE`) at the same time.

```python
_PLAYERS_STAT_RE = re.compile(r"(?<![A-Za-z])Players?:\s*(\d+)")
```

**Test:** assert `parse_server_status("maxPlayers: 64") is None`, alongside the existing `maxFPS` test.

---

## C2 — The background monitor never starts if Docker is briefly unreachable at boot — **P1**

**Where:** `backend/main.py:46–54`

```python
if await asyncio.to_thread(docker_service.ping):
    ...
    monitor_task = asyncio.create_task(_crash_monitor())
else:
    logger.warning("Docker daemon not reachable — ...")
```

The monitor is created **only if the very first ping succeeds**, and there is no retry. `_crash_monitor` is what performs crash recovery (`reconcile_and_recover`), scheduled restarts (`apply_scheduled_restarts`) and log pruning. So one transient failure at startup — a slow socket, a daemon still coming up after a host reboot, Docker Desktop mid-start on Windows — silently disables **all three, permanently**, until someone restarts the manager. The only trace is a single WARNING line.

The irony: the feature most needed after an unclean host reboot is auto-restart, and an unclean host reboot is exactly when this ping is most likely to fail.

**Fix:** always start the monitor; let each pass check `ping()` and no-op when the daemon is down (each pass already tolerates exceptions). The startup `remove_exited` cleanup can move into the first pass.

**Test:** start the app with `ping` monkeypatched to `False`, assert the monitor task exists; flip `ping` to `True` and assert a pass runs.

---

## C3 — A failed `docker info` is cached forever — **P1**

**Where:** `backend/services/docker_service.py:37–46`

```python
def daemon_info() -> dict:
    global _info
    if _info is None:
        try:
            _info = get_client().info()
        except DockerException:
            _info = {}        # <-- failure cached for the process lifetime
    return _info
```

Negative caching with no TTL. If the first `docker info` fails, `daemon_info()` returns `{}` forever, so `is_docker_desktop()` is permanently `False`. The user-visible effect: on **Windows**, the "Ports & firewall" panel defaults to the **Linux `ufw` command** — advice that cannot work on that host. The user has to notice and switch tabs.

**Fix:** cache only successes, or attach a short TTL (the same treatment `_asset_cache` already gets in `workshop_service`). `_self_mounts` (line ~44) has the same shape but is genuinely immutable per container, so it can stay.

**Test:** first call raises → second call (daemon back) returns real info.

---

## C4 — A failed API call makes the "Stored data" panel vanish — **P1**

**Where:** `frontend/src/views/InstanceDetail.vue:260–266`, plus `v-if="dataInfo"` on the card

```js
async function loadData() {
  try {
    dataInfo.value = await api(`/api/instances/${props.id}/data`)
  } catch {
    /* leave the card empty */      // <-- error swallowed
  }
}
```

`dataInfo` stays `null`, the `v-if` hides the entire card, and the user sees **no feature and no error** — indistinguishable from "this version doesn't have it". This is not hypothetical: it is precisely the failure mode the maintainer hit while looking for the feature ("I have searched for a button somewhere in the GUI but cannot find it"), and it would have been genuinely undiagnosable had the cause been a 500 rather than scrolling.

The same swallow-and-hide pattern appears in `loadStats`, `loadLogFiles` and `loadTemplates` in the same file.

**Fix:** keep the card rendered and show the error inside it (`dataError`), which is what every other panel in the app does. Audit the other three catches: silent is fine for *transient* polling (stats), never for *first load* of a feature.

---

## C5 — FastAPI validation errors render as `[object Object]` — **P2**

**Where:** `frontend/src/api.js:15–23`

`detail` is assumed to be a string, but FastAPI's **422** returns an *array of error objects*. `new ApiError(status, detail)` then does `super(detail || ...)`, which stringifies the array to `[object Object]`. Known live symptom: the wizard's config preview shows `// [object Object]` until the template is named (`POST /api/templates/preview` 422s on an empty `name`, which is `min_length=1`).

**Fix:** normalize in `api.js` — if `detail` is an array, join `d.loc.at(-1) + ': ' + d.msg`.

---

## A1 — Docker API call amplification on the 5-second poll — **P2**

**Where:** `instance_service.py:717` (`instance_view`), `container_status`, `instances_summary`; `Instances.vue` polls both endpoints every 5 s.

Two problems compound:

1. **`instance_view` calls `docker_service.ping()` per instance** — inside a list comprehension over every instance. Ten instances, ten pings, every poll.
2. **The same container state is derived repeatedly.** `container_status()` → `find_instance_container()` → `containers.list(filters=…)`, and docker-py's `list()` is *non-sparse*: it issues `GET /containers/json` **plus a full inspect per matching container**. Then `.reload()` inspects it *again*. `instances_summary` then calls `find_instance_container()` a second time for the same instance to read its logs.

Rough cost per instance per 5-second poll of the Instances page (which hits `/api/instances` **and** `/api/instances/summary`): **~8–10 Docker API round-trips**. With five servers that is on the order of 10 calls/second against the daemon, forever, for a page that is mostly idle. The 15-second monitor adds more.

**Fix:** one snapshot per request. A single `containers.list(all=True, filters={label: managed})` returns every managed container with its attrs; index it by `LABEL_INSTANCE_ID` and pass that map into `instance_view` / `instances_summary` / `instance_stats`. Ping once per request, not once per instance. This is a contained change — the call sites already funnel through two functions.

---

## A2 — `container.stats(stream=False)` blocks a worker for ~1–2 s — **P2**

**Where:** `instance_service.py:129–132`, called from `instance_stats` on every detail-page poll (5 s).

Docker's one-shot stats endpoint only answers **after it has collected two CPU samples**, so this call takes on the order of a second or two. It runs inside `asyncio.to_thread`, so it blocks a threadpool worker for that time, every five seconds, per open detail page.

**Fix:** decouple CPU/memory sampling from the status poll — e.g. refresh it in the background monitor and serve the last sample, or poll it on a slower cadence than the rest of the status. (Worth measuring first, but the two-sample behaviour is inherent to the endpoint.)

---

## A3 — God modules — **P2**

- **`backend/services/instance_service.py` — 1,433 lines**, carrying seven distinct responsibilities behind comment banners: config rendering · filesystem helpers · DB helpers · container lifecycle · status/log parsing · log files · stored-data wiping · scheduled restarts. The comment banners are effectively admitting the file wants to be several modules.
  Suggested split: `instance_lifecycle.py` (create/start/stop/recreate), `instance_status.py` (log parsing, state, stats), `instance_data.py` (profile/workshop/logs on disk), `instance_schedule.py`. The log-parsing half is pure and would become trivially testable in isolation — see **C1**.

- **`frontend/src/views/TemplateWizard.vue` — 1,410 lines**, holding four wizard steps, the Workshop search, the mod graph UI, version locking, import/export and three confirm dialogs in one `<script setup>`. Split by step (`ScenarioStep.vue`, `ModsStep.vue`, `SettingsStep.vue`, `SaveStep.vue`) with the spec as a shared model.

Neither is urgent; both raise the cost of every future change and every review.

---

## A4 — Duplicated job/progress/WebSocket machinery — **P3**

`steam_service.DownloadJob` and `image_service.PullJob` are the same object with different labels: identical `subscribers: list[asyncio.Queue]`, `snapshot()`, `subscribe()`/`unsubscribe()`, and the same broadcast loop. On top of that, three WebSocket endpoints (`instances_api.py:243`, `serverfiles_api.py:63`, `serverfiles_api.py:130`) each hand-roll the same accept → drain queue → handle disconnect pump.

**Fix:** one `ProgressJob` + `broker` in `services/jobs.py`, and one `stream_job(websocket, job)` helper. Removes ~100 lines and makes a third progress-reporting feature cheap.

---

## D1 — `Instance.container_id` is write-only — **P3**

**Where:** `models.py:50`, written at `instance_service.py:586`, and read *only* by a log statement.

Every container lookup in the codebase goes through Docker **labels** (`find_instance_container`), which is the right design — it survives manager restarts and container recreation. The column is stale by design (it holds a truncated id that changes whenever the container is recreated) and misleads anyone reading the schema into thinking it is the source of truth.

**Fix:** drop the column (additive-migration style: leave it, stop writing it, or clean it up in a migration), or delete the field outright. Do not start *using* it.

---

## D2 — Fields computed but never consumed — **P3**

Verified by grepping the frontend for every field the API emits:

| Producer | Field | Note |
|---|---|---|
| `workshop_service._row_summary` | `subscribers`, `rating` | scraped, serialized, never displayed |
| `instance_view` | `created_at` | never displayed |
| `instance_stats` | `server_mem_kb` | **parsed out of every log line** (`_MEM_RE`) and thrown away |
| `instance_stats` | `mem_limit_bytes` | fetched, never displayed |
| `instances_summary` | `public_address` | never displayed (per-server `connect` is used instead) |

None of it is harmful, but `server_mem_kb` costs a regex pass over a 400-line log tail on every poll to produce a number nobody reads. **Either surface these (server memory next to CPU would be genuinely useful) or stop computing them.** Pick one deliberately.

---

## D3 — Unbounded in-memory dicts — **P3**

- `instance_service._online_runs` (`:828`) — one entry per container id, never evicted. Bounded in practice by how many containers you ever create, but it grows for the process lifetime.
- `auth._attempts` (`auth.py:25`) — a `defaultdict(deque)` keyed by **client IP**, pruned only when that same IP returns. An internet-exposed GUI accumulates one deque per source IP indefinitely.

**Fix:** evict `_online_runs` when a container is removed (`delete_instance` already knows); sweep `_attempts` of empty/expired windows on each login (a few lines).

---

## P1 — There are no frontend tests, and no runner configured — **P2**

3,738 lines of Vue/JS — including `mods.js`, the **mod dependency-graph logic** (enable → transitive closure, disable → orphan detection, promote/demote) — have **zero** automated tests. `frontend/package.json` has no test script and no runner.

This is not theoretical debt. Two bugs reached production in the last week that a unit test would have caught in seconds:

- the "Stored data" checkboxes **silently dropped a selection** (`v-model` on an array reads the last-patched copy, so two ticks before a re-render collapse into one);
- `fmtBytes` had no GB tier, so a 3.4 GB mod folder rendered as `3242.5 MB`.

Both were caught by manually driving a browser — an expensive and unrepeatable way to find them.

**Fix:** add **vitest**. Start with the pure logic, where the value is highest and the cost is near zero: `mods.js` (graph operations), `status.js` (`serverStatus` matrix), `fmtBytes`/`fmtUptime`, and `api.js` error normalization (**C5**). Add `@vue/test-utils` for the wizard later. Wire it into the CI job that already builds the frontend.

---

## P2 / P3 / P5 — Tooling gaps — **P3**

- **No linters in CI.** The workflow runs `pytest` and a frontend build, nothing else. Add `ruff` (backend) and `eslint` (frontend) — both would have flagged the unused-variable and shadowing classes of error for free.
- **Unpinned Python dependencies:** `backend/requirements.txt` uses `>=` for all six. Image builds are therefore **not reproducible**, and a breaking FastAPI/SQLModel release lands straight in the published image without a code change. Pin with a lockfile (`pip-compile`/`uv`), or at minimum `~=` compatible-release pins.
- **Version is duplicated** in `backend/config.py:7` and `frontend/package.json` and bumped by hand each release — they *will* drift. Derive one from the other at build time, or read the version from a single file.
- **`.env.example` omits four supported variables:** `DATA_DIR`, `SERVERFILES_DIR`, `DOCKER_NETWORK`, `STATIC_DIR`. They are read by `config.py` but undocumented.
- **`scripts/windows/*.ps1` duplicate `Get-DockerCli` three times.** Minor, but it is the function most likely to need fixing (Docker CLI path changes).

---

## S1 — Session cookie is missing the `secure` flag — **P2**

**Where:** `auth.py:86–92` — the cookie is set `httponly=True, samesite="lax"`, but never `secure=True`.

The default deployment binds to `127.0.0.1`, so this is defensible out of the box; but the README actively encourages putting a TLS reverse proxy in front for remote use, and in that deployment the session cookie will still happily travel over a plaintext downgrade. Add a `SESSION_COOKIE_SECURE` env (default `false`, documented as "set true behind TLS") — or auto-enable it when the request scheme is https.

Related, and **fine as-is** but worth writing down: there is no CSRF token. `samesite=lax` plus JSON-only POSTs makes classic cross-site form submission impractical, so the residual risk is low; note it deliberately rather than by omission.

---

## Suggested order of work

**Sprint 1 — the silent failures (all small, all user-visible):**
1. **C1** anchor the player regex (+ test) — 15 min
2. **C3** stop caching `docker info` failures — 15 min
3. **C2** always start the background monitor — 30 min
4. **C4** show an error instead of hiding the panel — 30 min
5. **C5** normalize 422 detail in `api.js` — 20 min

**Sprint 2 — stop the bleeding on quality:**
6. **P1** vitest + tests for `mods.js`, `status.js`, formatters, `api.js`
7. **P2** ruff + eslint in CI
8. **P3** pin dependencies

**Sprint 3 — the hot path:**
9. **A1** single container snapshot per request (biggest real win)
10. **A2** decouple CPU/memory sampling

**Sprint 4 — structure, when touching these areas anyway:**
11. **A3** split `instance_service.py` (start with the pure log-parsing half)
12. **A4** shared job/WS machinery
13. **D1/D2/D3** dead state and unbounded dicts

---

## What is genuinely good (don't "fix" these)

- **Workshop scraping is correctly isolated**: pure `parse_*` functions, fixture-tested, network layer thin and mockable. It degrades to manual mod-id entry when the site changes.
- **Label-based container discovery** rather than stored ids — survives restarts and recreation.
- **Container recreation on drift** (ports, environment) is the right call, and the "never destroy a container we cannot inspect" guard in `_container_ports_match` / `_container_env_matches` is exactly the correct failure posture.
- **Additive-only DB migrations** in `models.init_db()` — crude but honest, and safe for a self-hosted app that users update by pulling an image.
- **The port model** (host == container == advertised, 1:1) is documented with the reason it must not be "simplified" back. Keep that comment.
