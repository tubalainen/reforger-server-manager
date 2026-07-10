"""Server templates: render the exact Arma Reforger config.json and persist.

A template is stored as its rendered config.json (the single source of truth
for what a server runs). The wizard both renders from a structured spec and
reads the pieces back out of a saved config for editing.

config.json schema: https://community.bistudio.com/wiki/Arma_Reforger:Server_Config
"""
import json

from pydantic import BaseModel, Field

DEFAULT_SUPPORTED_PLATFORMS = ["PLATFORM_PC", "PLATFORM_XBL", "PLATFORM_PSN"]


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

    # persistence (save games) — written only when enabled
    persistence_enabled: bool = False
    auto_save_interval: int = Field(default=10, ge=0, le=60)
    hive_id: int = Field(default=0, ge=0, le=16383)

    # rcon (optional; block written only when a password is set)
    rcon_password: str = ""
    rcon_permission: str = Field(default="admin", pattern="^(admin|monitor)$")
    rcon_max_clients: int = Field(default=16, ge=1, le=16)

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
        # disableNavmeshStreaming is a string ARRAY (not a bool): an empty
        # array disables streaming for all navmeshes; omit it when off (#28).
        if self.disable_navmesh_streaming:
            config["operating"]["disableNavmeshStreaming"] = []
        if self.persistence_enabled:
            config["game"]["gameProperties"]["persistence"] = {
                "autoSaveInterval": self.auto_save_interval,
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


def spec_from_config(config_json: str) -> dict:
    """Reconstruct the wizard's editable fields from a saved config.json.

    Returns a plain dict (not a validated TemplateSpec) so partial/legacy
    configs still load into the wizard for editing.
    """
    cfg = json.loads(config_json)
    game = cfg.get("game", {})
    props = game.get("gameProperties", {})
    operating = cfg.get("operating", {})
    rcon = cfg.get("rcon", {})
    return {
        "scenario_id": game.get("scenarioId", ""),
        "mods": game.get("mods", []),
        "game_name": game.get("name", ""),
        "password": game.get("password", ""),
        "admin_password": game.get("passwordAdmin", ""),
        "max_players": game.get("maxPlayers", 64),
        "visible": game.get("visible", True),
        "cross_platform": game.get("crossPlatform", True),
        "supported_platforms": game.get("supportedPlatforms", list(DEFAULT_SUPPORTED_PLATFORMS)),
        "admins": game.get("admins", []),
        "mods_required_by_default": game.get("modsRequiredByDefault", False),
        "battleye": props.get("battlEye", True),
        "server_max_view_distance": props.get("serverMaxViewDistance", 1600),
        "server_min_grass_distance": max(50, props.get("serverMinGrassDistance", 50)),
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
        "auto_save_interval": (props.get("persistence") or {}).get("autoSaveInterval", 10),
        "hive_id": (props.get("persistence") or {}).get("hiveId", 0),
        "rcon_password": rcon.get("password", ""),
        "rcon_permission": rcon.get("permission", "admin"),
        "rcon_max_clients": rcon.get("maxClients", 16),
    }
