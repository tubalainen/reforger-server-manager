"""Server templates: render the exact Arma Reforger config.json and persist.

A template is stored as its rendered config.json (the single source of truth
for what a server runs). The wizard both renders from a structured spec and
reads the pieces back out of a saved config for editing.

config.json schema: https://community.bistudio.com/wiki/Arma_Reforger:Server_Config
"""
import json

from pydantic import BaseModel, Field, field_validator

DEFAULT_SUPPORTED_PLATFORMS = ["PLATFORM_PC", "PLATFORM_XBL", "PLATFORM_PSN"]


def merge_patch(base: dict, patch: dict) -> dict:
    """Apply an RFC 7386 JSON Merge Patch to `base`, returning a new dict.

    Standard semantics: objects merge recursively, a null value deletes the key,
    and everything else (including arrays) replaces wholesale.
    """
    out = dict(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_patch(out[key], value)
        else:
            out[key] = value
    return out


def diff_patch(base: dict, target: dict) -> dict:
    """The minimal RFC 7386 merge patch that turns `base` into `target`.

    Inverse of merge_patch: merge_patch(base, diff_patch(base, target)) == target.
    Used to split a hand-edited config into "what the model already renders" and
    "what only the user knows about" (#29).
    """
    patch: dict = {}
    for key, value in target.items():
        if key not in base:
            patch[key] = value
        elif isinstance(value, dict) and isinstance(base[key], dict):
            sub = diff_patch(base[key], value)
            if sub:
                patch[key] = sub
        elif base[key] != value:
            patch[key] = value
    for key in base:
        if key not in target:
            patch[key] = None  # null = delete, per RFC 7386
    return patch


class ModEntry(BaseModel):
    modId: str
    name: str | None = None
    # A user-locked version (#60). None = follow the Workshop's latest release:
    # to_config then omits "version" so the server auto-updates the mod.
    version: str | None = None
    # Dependency metadata for the mod manager (#55). These never reach the
    # server's config.json (to_config strips mods to modId/name/version); they
    # are persisted separately in Template.mods_json for editing.
    explicit: bool = True          # user/scenario chose it directly (a "root")
    from_scenario: bool = False    # pulled in by the currently selected scenario
    dependencies: list[str] = []   # direct dependency modIds (graph edges)
    versions: list[str] = []       # published Workshop versions (lock picker, #60)
    # This asset publishes its own scenario(s). Used to flag a mod that would
    # add a *second* scenario as a plain addon (#69) — a server runs only one.
    provides_scenarios: bool = False
    # Sequence number of when the user added the mod, so the wizard's "sort as
    # added" can restore that order after a name sort (#105). None = added
    # before the counter existed (sorts as oldest, keeping current order).
    added_order: int | None = None


class WhitelistEntry(BaseModel):
    """A player allowed in when game.playerWhitelist is non-empty (#154).

    identityId is the player's *Bohemia account* identity id — not a Steam id.
    `name` is a label for the admin reading the list; the server matches on the
    id alone.
    """
    identityId: str = Field(min_length=1)
    name: str = ""

    @field_validator("identityId", "name")
    @classmethod
    def _trim(cls, value: str) -> str:
        return (value or "").strip()


class BanEntry(WhitelistEntry):
    """A player denied entry via game.playerBanList (#154)."""
    reason: str = ""

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        return (value or "").strip()


def _dedupe_players(entries: list) -> list:
    """Keep the first entry per identityId, preserving order."""
    seen: set[str] = set()
    out = []
    for entry in entries:
        key = entry.identityId.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


class LaunchParams(BaseModel):
    """Engine command-line launch parameters (Longbow's Advanced panel).

    Rendered to the server image's ARMA_PARAMS (+ ARMA_MAX_FPS). Every field is
    optional: None / False means the argument is omitted. Arg names are the
    exact Enfusion server args used by Longbow.
    """

    # valued args (-arg value); None -> omitted
    max_fps: int | None = Field(default=None, ge=10, le=1000)          # -> ARMA_MAX_FPS
    network_dynamic_simulation: int | None = None                       # -nds
    spatial_map_resolution: int | None = Field(default=None, ge=100, le=1000)  # -nwkResolution
    staggering_budget: int | None = None                                # -staggeringBudget
    streaming_budget: int | None = None                                 # -streamingBudget
    streams_delta: int | None = None                                    # -streamsDelta
    auto_reload_scenario: int | None = Field(default=None, ge=0)        # -autoreload (seconds)
    rpl_timeout_ms: int | None = None                                   # -rpl-timeout-ms
    freeze_check: int | None = None                                     # -freezeCheck
    freeze_check_mode: str | None = None                                # -freezeCheckMode
    debugger_address: str | None = None                                 # -debugger
    debugger_port: int | None = Field(default=None, ge=1, le=65535)     # -debuggerPort
    load_session_save: str | None = None                                # -loadSessionSave
    short_worker_count: int | None = Field(default=None, ge=1)          # -jobsysShortWorkerCount
    long_worker_count: int | None = Field(default=None, ge=1)           # -jobsysLongWorkerCount

    # switch args (-arg); False -> omitted
    verify_and_repair_addons: bool = False    # -addonsRepair
    auto_shutdown: bool = False               # -autoShutdown
    log_voting: bool = False                  # -logVoting
    ai_partial_sim: bool = False              # -aiPartialSim
    force_recreate_database: bool = False     # -createDB
    disable_shaders_build: bool = False       # -disableShadersBuild
    generate_shaders: bool = False            # -generateShaders
    rpl_encode_as_long_jobs: bool = False     # -rplEncodeAsLongJobs
    force_disable_night_grain: bool = False   # -forceDisableNightGrain
    no_backend: bool = False                  # -noBackend

    # escape hatch for any arg not modelled above
    extra_args: str = ""

    @field_validator("extra_args")
    @classmethod
    def _no_shell_metacharacters(cls, value: str) -> str:
        """Reject shell metacharacters in the free-form launch arguments (R10).

        These are rendered into ARMA_PARAMS and handed to the server image, which
        commonly expands them through a shell. Anything below would therefore run
        as a *command* rather than as an engine flag. Only an authenticated admin
        can reach this — and they already control Docker through the GUI — so this
        is not a privilege boundary; it keeps a stored template from being a
        command-execution vector and stops a typo turning into one.
        """
        bad = [c for c in ";|&<>`$\n\r" if c in value]
        if bad:
            raise ValueError(
                "extra_args may not contain shell metacharacters "
                f"({' '.join(repr(c) for c in sorted(set(bad)))}). "
                "Pass engine flags only, e.g. '-myFlag 123'."
            )
        return value

    # field -> exact engine arg for valued (excludes max_fps: it uses ARMA_MAX_FPS)
    _VALUED = {
        "network_dynamic_simulation": "nds",
        "spatial_map_resolution": "nwkResolution",
        "staggering_budget": "staggeringBudget",
        "streaming_budget": "streamingBudget",
        "streams_delta": "streamsDelta",
        "auto_reload_scenario": "autoreload",
        "rpl_timeout_ms": "rpl-timeout-ms",
        "freeze_check": "freezeCheck",
        "freeze_check_mode": "freezeCheckMode",
        "debugger_address": "debugger",
        "debugger_port": "debuggerPort",
        "load_session_save": "loadSessionSave",
        "short_worker_count": "jobsysShortWorkerCount",
        "long_worker_count": "jobsysLongWorkerCount",
    }
    _SWITCHES = {
        "verify_and_repair_addons": "addonsRepair",
        "auto_shutdown": "autoShutdown",
        "log_voting": "logVoting",
        "ai_partial_sim": "aiPartialSim",
        "force_recreate_database": "createDB",
        "disable_shaders_build": "disableShadersBuild",
        "generate_shaders": "generateShaders",
        "rpl_encode_as_long_jobs": "rplEncodeAsLongJobs",
        "force_disable_night_grain": "forceDisableNightGrain",
        "no_backend": "noBackend",
    }

    def render(self) -> tuple[str, int | None]:
        """Return (ARMA_PARAMS string, max_fps or None for ARMA_MAX_FPS)."""
        parts: list[str] = []
        for field, arg in self._VALUED.items():
            value = getattr(self, field)
            if value is not None and str(value) != "":
                parts.append(f"-{arg} {value}")
        for field, arg in self._SWITCHES.items():
            if getattr(self, field):
                parts.append(f"-{arg}")
        if self.extra_args.strip():
            parts.append(self.extra_args.strip())
        return " ".join(parts), self.max_fps


class TemplateSpec(BaseModel):
    """The structured input the wizard collects; renders to config.json."""

    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    scenario_id: str = Field(min_length=1)
    # Scenario display name for the wizard (#59); config.json only holds the
    # raw scenarioId, so this is persisted separately and never rendered.
    scenario_name: str = ""
    # Player count the scenario declares on the Workshop (#65). Seeds max_players
    # when a scenario is picked and backs the "recommended" hint; the user's
    # max_players always wins. Never rendered to config.json.
    scenario_player_count: int | None = Field(default=None, ge=1, le=256)
    mods: list[ModEntry] = []
    launch: LaunchParams = Field(default_factory=LaunchParams)

    # game settings
    game_name: str = "Arma Reforger Server"
    password: str = ""
    admin_password: str = ""
    max_players: int = Field(default=64, ge=1, le=256)
    visible: bool = True
    cross_platform: bool = True
    supported_platforms: list[str] = Field(default_factory=lambda: list(DEFAULT_SUPPORTED_PLATFORMS))
    admins: list[str] = []
    # Player access lists (#154). Both are written to config.json ONLY when
    # non-empty, so a template that uses neither renders exactly the config it
    # rendered before these existed.
    player_whitelist: list[WhitelistEntry] = []
    player_ban_list: list[BanEntry] = []
    mods_required_by_default: bool = False

    # gameProperties
    battleye: bool = True
    server_max_view_distance: int = Field(default=1600, ge=500, le=12000)
    # The server's JSON schema requires >= 50 for this field (see issue #28).
    server_min_grass_distance: int = Field(default=50, ge=50, le=150)
    network_view_distance: int = Field(default=1500, ge=500, le=5000)
    disable_third_person: bool = False
    fast_validation: bool = True
    von_disable_ui: bool = False
    von_disable_direct_speech_ui: bool = False
    von_can_transmit_cross_faction: bool = False

    # operating (advanced)
    lobby_player_synchronise: bool = True
    disable_navmesh_streaming: bool = False
    disable_server_shutdown: bool = False
    disable_crash_reporter: bool = False
    disable_ai: bool = False
    player_save_time: int = Field(default=120, ge=1, le=65535)
    ai_limit: int = Field(default=-1, ge=-1, le=1000)
    slot_reservation_timeout: int = Field(default=60, ge=5, le=300)
    join_queue_max_size: int = Field(default=0, ge=0, le=50)

    # persistence (save games) — the whole block is written only when enabled.
    # Ranges and defaults are the engine's own (Server Config wiki, 1.6.0).
    # Note: load_/keep_session_save are the *config* keys under persistence, not
    # LaunchParams.load_session_save, which is the -loadSessionSave engine arg.
    persistence_enabled: bool = False
    auto_save_interval: int = Field(default=10, ge=0, le=60)
    save_retention: int = Field(default=10, ge=1, le=128)
    load_session_save: bool = True
    keep_session_save: bool = False
    hive_id: int = Field(default=0, ge=0, le=16383)

    # rcon (optional; block written only when a password is set)
    rcon_password: str = ""
    rcon_permission: str = Field(default="admin", pattern="^(admin|monitor)$")
    rcon_max_clients: int = Field(default=16, ge=1, le=16)

    # Config keys the GUI doesn't model — scenario-specific gameProperties, or
    # anything Bohemia adds faster than this wizard does — held as an RFC 7386
    # merge patch and re-applied over every render so hand-edits survive a wizard
    # save instead of being dropped by to_config's dict literal (#29).
    extras: dict = {}

    @field_validator("admins")
    @classmethod
    def _clean_admins(cls, value: list[str]) -> list[str]:
        """Trim, drop blanks and de-duplicate the admin id list (#154).

        Deliberately does NOT reject an id whose shape we don't recognise, nor
        cap the list at the 20 the game accepts: a config.json import or a
        hand-edit must still load for editing, and Bohemia can change what an id
        looks like faster than this model does. The wizard validates shapes as
        you type and warns past 20 — that is where a human can react to it.
        """
        seen: set[str] = set()
        out: list[str] = []
        for entry in value:
            admin = str(entry or "").strip()
            key = admin.lower()
            if not admin or key in seen:
                continue
            seen.add(key)
            out.append(admin)
        return out

    @field_validator("player_whitelist", "player_ban_list")
    @classmethod
    def _clean_players(cls, value: list) -> list:
        return _dedupe_players(value)

    def to_config(self) -> dict:
        """Render the full server config.json.

        Bind/public/A2S/RCON ports are left at Reforger defaults here; the
        instance layer overrides them per server when a container is created.
        """
        mods = [
            {k: v for k, v in {
                "modId": m.modId, "name": m.name, "version": m.version
            }.items() if v is not None}
            for m in self.mods
        ]
        config: dict = {
            "bindAddress": "",
            "bindPort": 2001,
            "publicAddress": "",
            "publicPort": 2001,
            "a2s": {"address": "0.0.0.0", "port": 17777},
            "game": {
                "name": self.game_name,
                "password": self.password,
                "passwordAdmin": self.admin_password,
                "admins": self.admins,
                "scenarioId": self.scenario_id,
                "maxPlayers": self.max_players,
                "visible": self.visible,
                "crossPlatform": self.cross_platform,
                "supportedPlatforms": self.supported_platforms,
                "gameProperties": {
                    "serverMaxViewDistance": self.server_max_view_distance,
                    "serverMinGrassDistance": self.server_min_grass_distance,
                    "networkViewDistance": self.network_view_distance,
                    "battlEye": self.battleye,
                    "disableThirdPerson": self.disable_third_person,
                    "fastValidation": self.fast_validation,
                    "VONDisableUI": self.von_disable_ui,
                    "VONDisableDirectSpeechUI": self.von_disable_direct_speech_ui,
                    "VONCanTransmitCrossFaction": self.von_can_transmit_cross_faction,
                },
                "mods": mods,
                "modsRequiredByDefault": self.mods_required_by_default,
            },
            "operating": {
                "lobbyPlayerSynchronise": self.lobby_player_synchronise,
                "disableServerShutdown": self.disable_server_shutdown,
                "disableCrashReporter": self.disable_crash_reporter,
                "disableAI": self.disable_ai,
                "playerSaveTime": self.player_save_time,
                "aiLimit": self.ai_limit,
                "slotReservationTimeout": self.slot_reservation_timeout,
                "joinQueue": {"maxSize": self.join_queue_max_size},
            },
        }
        # Player access lists (#154). Written only when they hold something, so
        # a template that names no whitelisted or banned player renders exactly
        # the config it rendered before this feature existed — nobody who
        # doesn't use the lists can be affected by them. Both entry shapes are
        # written in full (empty strings included) rather than pruned, so the
        # server sees the documented shape whether or not a name was filled in.
        if self.player_whitelist:
            config["game"]["playerWhitelist"] = [
                {"identityId": p.identityId, "name": p.name} for p in self.player_whitelist
            ]
        if self.player_ban_list:
            config["game"]["playerBanList"] = [
                {"identityId": p.identityId, "name": p.name, "reason": p.reason}
                for p in self.player_ban_list
            ]
        # disableNavmeshStreaming is a string ARRAY (not a bool): an empty
        # array disables streaming for all navmeshes; omit it when off (#28).
        if self.disable_navmesh_streaming:
            config["operating"]["disableNavmeshStreaming"] = []
        if self.persistence_enabled:
            # Every documented persistence key is written (#156) — the block used
            # to carry only autoSaveInterval/hiveId, so save retention and the two
            # session-save switches silently stayed at the engine default however
            # the template was configured. databases/storages stay as empty
            # objects: they are free-form named overrides with no GUI, and a
            # hand-written one survives through `extras`.
            config["game"]["gameProperties"]["persistence"] = {
                "autoSaveInterval": self.auto_save_interval,
                "saveRetention": self.save_retention,
                "loadSessionSave": self.load_session_save,
                "keepSessionSave": self.keep_session_save,
                "hiveId": self.hive_id,
                "databases": {},
                "storages": {},
            }
        if self.rcon_password:
            config["rcon"] = {
                "address": "0.0.0.0",
                "port": 19999,
                "password": self.rcon_password,
                "permission": self.rcon_permission,
                "blacklist": [],
                "whitelist": [],
                "maxClients": self.rcon_max_clients,
            }
        # The user's own keys win over the rendered defaults. Applying last means
        # every consumer of to_config (render_config_json, /preview, and the
        # instance layer via template.config_json) is overlay-aware for free.
        if self.extras:
            config = merge_patch(config, self.extras)
        return config


def render_config_json(spec: TemplateSpec) -> str:
    return json.dumps(spec.to_config(), indent=2)


def persistence_summary(config_json: str) -> dict:
    """{persistence: bool, hive_id: int|None} — the persistent-save target.

    Used to warn when swapping an instance to a template that writes to a
    different save (a different hiveId), or none at all (issue #31).
    """
    try:
        props = ((json.loads(config_json).get("game") or {}).get("gameProperties") or {})
    except (ValueError, AttributeError):
        return {"persistence": False, "hive_id": None}
    p = props.get("persistence")
    if not p:
        return {"persistence": False, "hive_id": None}
    return {"persistence": True, "hive_id": p.get("hiveId", 0)}


def _players_from_config(raw, reason: bool = False) -> list[dict]:
    """Read a playerWhitelist / playerBanList back out of a config.json (#154).

    Lenient by design — this feeds the wizard, so a hand-written list must still
    open for editing: anything that isn't a list of objects with an identityId
    is skipped rather than raised on.
    """
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        identity = str(entry.get("identityId") or "").strip()
        if not identity:
            continue
        player = {"identityId": identity, "name": str(entry.get("name") or "").strip()}
        if reason:
            player["reason"] = str(entry.get("reason") or "").strip()
        out.append(player)
    return out


def spec_from_config(config_json: str, clamp: bool = True) -> dict:
    """Reconstruct the wizard's editable fields from a saved config.json.

    Returns a plain dict (not a validated TemplateSpec) so partial/legacy
    configs still load into the wizard for editing.

    clamp=True nudges out-of-range legacy values into the model's range so an old
    template still opens. Pass clamp=False when validating a hand-edited config
    (#29): there the user's exact value must reach the model, or an invalid one
    gets silently "fixed" here, diffed against the fixed baseline, and smuggled
    back in as an `extras` override that the server then rejects.
    """
    cfg = json.loads(config_json)
    game = cfg.get("game", {})
    props = game.get("gameProperties", {})
    operating = cfg.get("operating", {})
    rcon = cfg.get("rcon", {})
    # A config written before #156 has only some of the persistence keys; each
    # missing one falls back to the engine default, which is what the server was
    # running with anyway.
    persistence = props.get("persistence") or {}
    return {
        "scenario_id": game.get("scenarioId", ""),
        # Neither is part of config.json; both are filled from the DB row (and,
        # for a fresh import, from the Workshop by the wizard's hydration pass).
        "scenario_name": "",
        "scenario_player_count": None,

        "mods": game.get("mods", []),
        "game_name": game.get("name", ""),
        "password": game.get("password", ""),
        "admin_password": game.get("passwordAdmin", ""),
        "max_players": game.get("maxPlayers", 64),
        "visible": game.get("visible", True),
        "cross_platform": game.get("crossPlatform", True),
        "supported_platforms": game.get("supportedPlatforms", list(DEFAULT_SUPPORTED_PLATFORMS)),
        "admins": game.get("admins", []),
        "player_whitelist": _players_from_config(game.get("playerWhitelist")),
        "player_ban_list": _players_from_config(game.get("playerBanList"), reason=True),
        "mods_required_by_default": game.get("modsRequiredByDefault", False),
        "battleye": props.get("battlEye", True),
        "server_max_view_distance": props.get("serverMaxViewDistance", 1600),
        "server_min_grass_distance": (
            max(50, props.get("serverMinGrassDistance", 50))
            if clamp
            else props.get("serverMinGrassDistance", 50)
        ),
        "network_view_distance": props.get("networkViewDistance", 1500),
        "disable_third_person": props.get("disableThirdPerson", False),
        "fast_validation": props.get("fastValidation", True),
        "von_disable_ui": props.get("VONDisableUI", False),
        "von_disable_direct_speech_ui": props.get("VONDisableDirectSpeechUI", False),
        "von_can_transmit_cross_faction": props.get("VONCanTransmitCrossFaction", False),
        "lobby_player_synchronise": operating.get("lobbyPlayerSynchronise", True),
        # array present (even empty) = navmesh streaming disabled
        "disable_navmesh_streaming": "disableNavmeshStreaming" in operating,
        "disable_server_shutdown": operating.get("disableServerShutdown", False),
        "disable_crash_reporter": operating.get("disableCrashReporter", False),
        "disable_ai": operating.get("disableAI", False),
        "player_save_time": operating.get("playerSaveTime", 120),
        "ai_limit": operating.get("aiLimit", -1),
        "slot_reservation_timeout": operating.get("slotReservationTimeout", 60),
        "join_queue_max_size": (operating.get("joinQueue") or {}).get("maxSize", 0),
        "persistence_enabled": "persistence" in props,
        "auto_save_interval": persistence.get("autoSaveInterval", 10),
        "save_retention": persistence.get("saveRetention", 10),
        "load_session_save": persistence.get("loadSessionSave", True),
        "keep_session_save": persistence.get("keepSessionSave", False),
        "hive_id": persistence.get("hiveId", 0),
        "rcon_password": rcon.get("password", ""),
        "rcon_permission": rcon.get("permission", "admin"),
        "rcon_max_clients": rcon.get("maxClients", 16),
    }
