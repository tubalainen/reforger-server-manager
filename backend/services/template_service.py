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
    version: str | None = None


class TemplateSpec(BaseModel):
    """The structured input the wizard collects; renders to config.json."""

    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    scenario_id: str = Field(min_length=1)
    mods: list[ModEntry] = []

    # game settings
    game_name: str = "Arma Reforger Server"
    password: str = ""
    admin_password: str = ""
    max_players: int = Field(default=64, ge=1, le=256)
    visible: bool = True
    cross_platform: bool = True
    supported_platforms: list[str] = Field(default_factory=lambda: list(DEFAULT_SUPPORTED_PLATFORMS))
    admins: list[str] = []

    # gameProperties
    battleye: bool = True
    server_max_view_distance: int = Field(default=1600, ge=500, le=10000)
    server_min_grass_distance: int = Field(default=0, ge=0, le=150)
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
    player_save_time: int = Field(default=120, ge=0, le=86400)
    ai_limit: int = Field(default=-1, ge=-1, le=1000)
    slot_reservation_timeout: int = Field(default=60, ge=5, le=6000)

    # rcon (optional)
    rcon_password: str = ""

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
            },
            "operating": {
                "lobbyPlayerSynchronise": self.lobby_player_synchronise,
                "disableNavmeshStreaming": self.disable_navmesh_streaming,
                "disableServerShutdown": self.disable_server_shutdown,
                "disableCrashReporter": self.disable_crash_reporter,
                "playerSaveTime": self.player_save_time,
                "aiLimit": self.ai_limit,
                "slotReservationTimeout": self.slot_reservation_timeout,
            },
        }
        if self.rcon_password:
            config["rcon"] = {
                "address": "0.0.0.0",
                "port": 19999,
                "password": self.rcon_password,
                "permission": "admin",
                "maxClients": 16,
            }
        return config


def render_config_json(spec: TemplateSpec) -> str:
    return json.dumps(spec.to_config(), indent=2)


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
        "battleye": props.get("battlEye", True),
        "server_max_view_distance": props.get("serverMaxViewDistance", 1600),
        "server_min_grass_distance": props.get("serverMinGrassDistance", 0),
        "network_view_distance": props.get("networkViewDistance", 1500),
        "disable_third_person": props.get("disableThirdPerson", False),
        "fast_validation": props.get("fastValidation", True),
        "von_disable_ui": props.get("VONDisableUI", False),
        "von_disable_direct_speech_ui": props.get("VONDisableDirectSpeechUI", False),
        "von_can_transmit_cross_faction": props.get("VONCanTransmitCrossFaction", False),
        "lobby_player_synchronise": operating.get("lobbyPlayerSynchronise", True),
        "disable_navmesh_streaming": operating.get("disableNavmeshStreaming", False),
        "disable_server_shutdown": operating.get("disableServerShutdown", False),
        "disable_crash_reporter": operating.get("disableCrashReporter", False),
        "player_save_time": operating.get("playerSaveTime", 120),
        "ai_limit": operating.get("aiLimit", -1),
        "slot_reservation_timeout": operating.get("slotReservationTimeout", 60),
        "rcon_password": rcon.get("password", ""),
    }
