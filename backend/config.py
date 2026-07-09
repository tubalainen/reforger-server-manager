"""All runtime configuration, read from environment (.env via docker compose)."""
import os
import secrets
from dataclasses import dataclass

APP_NAME = "Reforger Server Manager"
APP_VERSION = "0.18.1"

# Steam app IDs for the Arma Reforger Dedicated Server
STEAM_APPID_STABLE = "1874900"
STEAM_APPID_EXPERIMENTAL = "1890870"

BRANCHES = {
    "stable": {"app_id": STEAM_APPID_STABLE, "label": "Stable"},
    "experimental": {"app_id": STEAM_APPID_EXPERIMENTAL, "label": "Experimental"},
}


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var; anything but a clear 'false' keeps the default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _port_range(raw: str | None, fallback: tuple[int, int]) -> tuple[int, int]:
    """Parse 'LO-HI' into a tuple; fall back on any malformed input."""
    try:
        lo_s, hi_s = (raw or "").split("-", 1)
        lo, hi = int(lo_s), int(hi_s)
    except ValueError:
        return fallback
    if lo <= 0 or lo > hi:
        return fallback
    return lo, hi


@dataclass
class Settings:
    admin_username: str
    admin_password: str
    auth_enabled: bool
    session_secret: str
    session_ttl_hours: int
    data_dir: str
    serverfiles_dir: str
    steamcmd_timeout_minutes: int
    log_retention_days: int
    static_dir: str
    docker_network: str
    reforger_server_image: str
    steamcmd_image: str
    public_address: str
    game_port_range: tuple[int, int]
    a2s_port_range: tuple[int, int]
    rcon_port_range: tuple[int, int]
    session_secret_generated: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("SESSION_SECRET", "").strip()
        generated = not secret
        if generated:
            secret = secrets.token_hex(32)
        return cls(
            admin_username=os.environ.get("ADMIN_USERNAME", "").strip(),
            admin_password=os.environ.get("ADMIN_PASSWORD", "").strip(),
            # Built-in login on by default; disable ONLY behind a reverse proxy
            # that enforces auth (issue #37).
            auth_enabled=_env_bool("AUTH_ENABLED", True),
            session_secret=secret,
            session_secret_generated=generated,
            session_ttl_hours=int(os.environ.get("SESSION_TTL_HOURS", "168")),
            data_dir=os.environ.get("DATA_DIR", "./data"),
            serverfiles_dir=os.environ.get("SERVERFILES_DIR", "/serverfiles"),
            steamcmd_timeout_minutes=int(os.environ.get("STEAMCMD_TIMEOUT_MINUTES", "60")),
            log_retention_days=int(os.environ.get("LOG_RETENTION_DAYS", "14")),
            static_dir=os.environ.get("STATIC_DIR", ""),
            docker_network=os.environ.get("DOCKER_NETWORK", "reforger-net"),
            reforger_server_image=os.environ.get(
                "REFORGER_SERVER_IMAGE", "ghcr.io/acemod/arma-reforger:latest"
            ),
            steamcmd_image=os.environ.get("STEAMCMD_IMAGE", "steamcmd/steamcmd:latest"),
            public_address=os.environ.get("PUBLIC_ADDRESS", "").strip(),
            game_port_range=_port_range(os.environ.get("GAME_PORT_RANGE"), (2001, 2020)),
            a2s_port_range=_port_range(os.environ.get("A2S_PORT_RANGE"), (17777, 17796)),
            rcon_port_range=_port_range(os.environ.get("RCON_PORT_RANGE"), (19999, 20018)),
        )


settings = Settings.from_env()
