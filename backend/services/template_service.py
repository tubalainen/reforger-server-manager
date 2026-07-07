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
    network_view_distance: int = Field(default=1500, ge=500, le=5000)

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
                    "networkViewDistance": self.network_view_distance,
                    "battlEye": self.battleye,
                },
                "mods": mods,
            },
            "operating": {
                "lobbyPlayerSynchronise": True,
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
        "network_view_distance": props.get("networkViewDistance", 1500),
        "rcon_password": rcon.get("password", ""),
    }
