"""Arma Reforger Workshop access.

There is no official Workshop API. The site (reforger.armaplatform.com) is a
Next.js app that embeds all page data in a `__NEXT_DATA__` <script> tag, so we
fetch pages and parse that JSON. All parsing lives in pure functions
(parse_search / parse_asset) that are unit-tested against saved fixtures; the
network layer is thin and mockable. Everything degrades gracefully: on any
scrape failure the caller falls back to manual mod-id entry.
"""
import json
import logging
import re
import time

import httpx

logger = logging.getLogger("manager.workshop")

BASE_URL = "https://reforger.armaplatform.com"
USER_AGENT = "Mozilla/5.0 (reforger-server-manager)"
REQUEST_TIMEOUT = 15.0

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)

# Asset ids are 16 hex digits; users may paste a full URL or "{id}-slug".
_ASSET_ID_RE = re.compile(r"([0-9A-Fa-f]{16})")

_ASSET_CACHE_TTL = 600  # seconds


# --------------------------------------------------------------------------- #
# Pure parsing (unit-tested against fixtures — no network)
# --------------------------------------------------------------------------- #

def extract_next_data(html: str) -> dict:
    """Return the parsed __NEXT_DATA__ pageProps, or raise ValueError."""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__ payload in page")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"__NEXT_DATA__ is not valid JSON: {exc}") from exc
    props = data.get("props", {}).get("pageProps")
    if not isinstance(props, dict):
        raise ValueError("__NEXT_DATA__ missing props.pageProps")
    return props


def extract_build_id(html: str) -> str | None:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1)).get("buildId")
    except json.JSONDecodeError:
        return None


def _row_summary(row: dict) -> dict:
    """Slim a search/detail asset row down to what the UI needs."""
    tags = [t.get("name") if isinstance(t, dict) else t for t in row.get("tags") or []]
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("type"),
        "version": row.get("currentVersionNumber"),
        "size": row.get("currentVersionSize") or 0,
        "subscribers": row.get("subscriberCount") or 0,
        "rating": row.get("averageRating"),
        "author": (row.get("author") or {}).get("username"),
        "tags": tags,
        # Heuristic hint for the scenario picker; authoritative list is on detail
        "has_scenarios": any("SCENARIO" in (t or "").upper() for t in tags),
    }


def parse_search(page_props: dict) -> dict:
    """Parse a /workshop listing page into {count, rows:[summary]}."""
    assets = page_props.get("assets") or {}
    rows = assets.get("rows")
    if not isinstance(rows, list):
        raise ValueError("search page missing assets.rows")
    return {
        "count": assets.get("count", len(rows)),
        "rows": [_row_summary(r) for r in rows],
    }


def parse_asset(page_props: dict) -> dict:
    """Parse a /workshop/{id} detail page into a normalized asset dict."""
    asset = page_props.get("asset")
    if not isinstance(asset, dict) or not asset.get("id"):
        raise ValueError("asset page missing props.pageProps.asset")
    detail = page_props.get("assetVersionDetail") or {}

    scenarios = []
    for sc in detail.get("scenarios") or []:
        game_id = sc.get("gameId")
        if game_id:
            scenarios.append({
                "scenario_id": game_id,
                "name": sc.get("name") or game_id,
                "game_mode": sc.get("gameMode"),
                "player_count": sc.get("playerCount"),
            })

    dependencies = []
    for dep in detail.get("dependencies") or []:
        dep_asset = dep.get("asset") or {}
        if dep_asset.get("id"):
            dependencies.append({
                "id": dep_asset["id"],
                "name": dep_asset.get("name"),
                "version": dep.get("version"),
                "size": dep.get("totalFileSize") or 0,
            })

    # Published version history (newest first) so the user can lock a mod to a
    # specific version instead of following the latest release (#60).
    versions = [
        v["version"] for v in asset.get("versions") or []
        if isinstance(v, dict) and v.get("version")
    ]

    summary = _row_summary(asset)
    summary["scenarios"] = scenarios
    summary["dependencies"] = dependencies
    summary["versions"] = versions
    return summary


def normalize_asset_id(raw: str) -> str | None:
    """Accept a bare id, '{id}-slug', or a full workshop URL."""
    m = _ASSET_ID_RE.search(raw or "")
    return m.group(1).upper() if m else None


# --------------------------------------------------------------------------- #
# Network layer (thin, mockable)
# --------------------------------------------------------------------------- #

class WorkshopService:
    def __init__(self):
        self._build_id: str | None = None
        self._asset_cache: dict[str, tuple[float, dict]] = {}

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    def _get_page_props(self, path: str, params: dict | None = None) -> dict:
        with self._client() as client:
            resp = client.get(f"{BASE_URL}{path}", params=params)
            resp.raise_for_status()
            html = resp.text
        if self._build_id is None:
            self._build_id = extract_build_id(html)
        return extract_next_data(html)

    def search(self, query: str, page: int = 1) -> dict:
        """Search the Workshop. Raises WorkshopError on any failure."""
        try:
            props = self._get_page_props("/workshop", {"search": query, "page": page})
            return parse_search(props)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Workshop search failed (query=%r): %s", query, exc)
            raise WorkshopError(str(exc)) from exc

    def get_asset(self, asset_id: str, use_cache: bool = True) -> dict:
        """Fetch and normalize one asset. Raises WorkshopError on failure."""
        asset_id = normalize_asset_id(asset_id) or asset_id
        if use_cache:
            cached = self._asset_cache.get(asset_id)
            if cached and time.time() - cached[0] < _ASSET_CACHE_TTL:
                return cached[1]
        try:
            props = self._get_page_props(f"/workshop/{asset_id}")
            asset = parse_asset(props)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Workshop asset fetch failed (id=%s): %s", asset_id, exc)
            raise WorkshopError(str(exc)) from exc
        self._asset_cache[asset_id] = (time.time(), asset)
        return asset

    def resolve_dependencies(self, asset_id: str) -> dict:
        """Resolve an asset + its full recursive dependency tree.

        Returns {asset, root, mods, missing, total_size} where `mods` is a flat,
        deduped list of {modId, name, version, versions, dependencies:[ids]}
        (the root asset first): `version` is the current Workshop release,
        `versions` the published history for the version-lock picker (#60),
        `dependencies` each mod's direct dependency ids (the graph edges the
        mod manager needs, #55), `root` the requested asset's id, and `missing`
        lists ids that couldn't be fetched.
        """
        root = self.get_asset(asset_id)
        mods: dict[str, dict] = {}
        missing: list[str] = []
        total_size = 0

        def add(entry: dict, dep_ids: list[str]) -> None:
            mid = entry["id"]
            if mid not in mods:
                mods[mid] = {
                    "modId": mid,
                    "name": entry.get("name"),
                    "version": entry.get("version"),
                    "versions": entry.get("versions") or [],
                    "dependencies": dep_ids,
                }
            elif dep_ids and not mods[mid]["dependencies"]:
                # Fill edges once we learn them (a mod first seen as a bare dep).
                mods[mid]["dependencies"] = dep_ids

        def dep_ids_of(asset: dict) -> list[str]:
            return [d["id"] for d in asset.get("dependencies") or [] if d.get("id")]

        add(root, dep_ids_of(root))
        total_size += root.get("size") or 0

        # BFS over dependencies; each asset page already lists its direct deps.
        seen: set[str] = {root["id"]}
        queue = list(root.get("dependencies") or [])
        while queue:
            dep = queue.pop(0)
            dep_id = dep["id"]
            if dep_id in seen:
                continue
            seen.add(dep_id)
            total_size += dep.get("size") or 0
            try:
                child = self.get_asset(dep_id)
            except WorkshopError:
                missing.append(dep_id)
                add(dep, [])  # keep it listed so the user sees the gap
                continue
            add(
                {"id": dep_id,
                 "name": dep.get("name") or child.get("name"),
                 "version": dep.get("version") or child.get("version"),
                 "versions": child.get("versions")},
                dep_ids_of(child),
            )
            queue.extend(child.get("dependencies") or [])

        return {
            "asset": root,
            "root": root["id"],
            "mods": list(mods.values()),
            "missing": missing,
            "total_size": total_size,
        }


class WorkshopError(Exception):
    """Raised when the Workshop cannot be reached or parsed."""


workshop = WorkshopService()
