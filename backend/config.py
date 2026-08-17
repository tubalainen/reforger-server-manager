"""All runtime configuration, read from environment (.env via docker compose)."""
import ipaddress
import os
import secrets
from dataclasses import dataclass, field

APP_NAME = "Reforger Server Manager"
APP_VERSION = "0.52.0"

# The password shipped in .env.example. Refusing to start with it (when exposed)
# is what stops a "just ran docker compose up" box from facing the internet on
# admin/change-me-now (security review R3).
EXAMPLE_PASSWORD = "change-me-now"

# WEB_BIND values that keep the GUI on the local host only. The app itself always
# binds 0.0.0.0 *inside* its container; WEB_BIND is the host interface compose
# publishes the port on, and .env is loaded into the container, so it is the best
# in-process signal of whether the operator meant to expose the GUI.
_LOOPBACK_BINDS = {"", "127.0.0.1", "localhost", "::1", "[::1]"}

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


def _parse_networks(raw: str | None) -> tuple[tuple, tuple[str, ...]]:
    """Parse 'IP,CIDR,...' into (networks, malformed entries).

    A bare address is accepted as a single-host network. Malformed entries are
    returned rather than raised so startup can warn about them by name instead
    of silently trusting a shorter list than the operator intended.
    """
    networks = []
    invalid = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            invalid.append(part)
    return tuple(networks), tuple(invalid)


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
    web_bind: str
    # Operator's explicit "yes, a reverse proxy in front enforces auth" for an
    # exposed, login-disabled run (security review R2). Nothing else lets the
    # app start in that configuration.
    auth_delegated_ack: bool
    session_secret: str
    session_ttl_hours: int
    session_cookie_secure: bool
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
    # Reverse proxies whose X-Forwarded-For may be believed when identifying the
    # client for login throttling (security review R4). Empty = trust nothing and
    # always use the direct peer, which is the safe default for a direct bind.
    trusted_proxies: tuple = ()
    trusted_proxies_invalid: tuple[str, ...] = field(default=())
    # Interactive API docs (/docs, /redoc, /openapi.json). Off by default: they
    # enumerate every endpoint of a Docker-controlling API to anyone who asks
    # (security review R6).
    api_docs: bool = False
    # Extra browser origins accepted on state-changing requests, for a proxy that
    # rewrites Host. Same-origin always works without listing anything (R7).
    allowed_origins: tuple[str, ...] = field(default=())
    # Override the Content-Security-Policy if a deployment needs a looser one.
    content_security_policy: str = ""
    # Apply no-new-privileges to the GAME SERVER containers (security review R8).
    # Off by default: it broke player connections in v0.45.0 (#150). The flag is
    # inherited by every child process and cannot be unset, and Reforger's
    # BattlEye runs as a child of the server — so the server starts normally and
    # then refuses players, which is a far worse failure than the escalation this
    # was defending against. Our own short-lived helper containers (steamcmd,
    # cleanup) keep it unconditionally; they have no anti-cheat and are proven
    # working. Set true to opt back in.
    instance_no_new_privileges: bool = False
    # How game server containers attach to the network (#150).
    #
    #   host   — the server binds its UDP ports straight on the host. No NAT, no
    #            docker-proxy, and the server sees each player's REAL address.
    #            This is the normal way to run a game server in Docker.
    #   bridge — the old behaviour: ports published through Docker's NAT, with a
    #            userland docker-proxy in front of every UDP port. That rewrites
    #            source addresses to the bridge gateway, which game networking and
    #            BattlEye do not tolerate.
    #   auto   — host everywhere except Docker Desktop (Windows/macOS), whose VM
    #            cannot offer real host networking, so bridge is the only option
    #            there.
    instance_network_mode: str = "auto"
    # Optional one-click mod ordering (#164). An OpenAI-compatible chat
    # completions URL — a free Gemini or OpenRouter key, or a local Ollama.
    # EMPTY BY DEFAULT, which means the manager makes no AI request at all and
    # the wizard offers only the copy-the-prompt route, where the mod list
    # leaves through the user's own browser and nothing is configured here.
    ai_order_url: str = ""
    ai_order_model: str = ""
    ai_order_key: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("SESSION_SECRET", "").strip()
        generated = not secret
        if generated:
            secret = secrets.token_hex(32)
        trusted, trusted_bad = _parse_networks(os.environ.get("TRUSTED_PROXIES"))
        origins = tuple(
            o.strip().rstrip("/")
            for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        )
        return cls(
            trusted_proxies=trusted,
            trusted_proxies_invalid=trusted_bad,
            api_docs=_env_bool("API_DOCS", False),
            allowed_origins=origins,
            content_security_policy=os.environ.get("CONTENT_SECURITY_POLICY", "").strip(),
            instance_no_new_privileges=_env_bool("INSTANCE_NO_NEW_PRIVILEGES", False),
            instance_network_mode=os.environ.get("INSTANCE_NETWORK_MODE", "auto").strip().lower(),
            admin_username=os.environ.get("ADMIN_USERNAME", "").strip(),
            admin_password=os.environ.get("ADMIN_PASSWORD", "").strip(),
            # Built-in login on by default; disable ONLY behind a reverse proxy
            # that enforces auth (issue #37) — and, when exposed, only with
            # AUTH_DELEGATED_ACK=true (security review R2).
            auth_enabled=_env_bool("AUTH_ENABLED", True),
            web_bind=os.environ.get("WEB_BIND", "").strip(),
            auth_delegated_ack=_env_bool("AUTH_DELEGATED_ACK", False),
            session_secret=secret,
            session_secret_generated=generated,
            session_ttl_hours=int(os.environ.get("SESSION_TTL_HOURS", "168")),
            # Only meaningful behind TLS; a secure cookie is never sent over the
            # plain-HTTP localhost default, so it cannot be on by default (#88).
            session_cookie_secure=_env_bool("SESSION_COOKIE_SECURE", False),
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
            ai_order_url=os.environ.get("AI_ORDER_URL", "").strip(),
            ai_order_model=os.environ.get("AI_ORDER_MODEL", "").strip(),
            ai_order_key=os.environ.get("AI_ORDER_KEY", "").strip(),
        )


def web_exposed(s: Settings) -> bool:
    """True when WEB_BIND publishes the GUI beyond the local host.

    Unset/empty means compose's 127.0.0.1 default, i.e. not exposed. Any other
    address (0.0.0.0, a LAN or public IP) counts as exposed.
    """
    return s.web_bind.strip().lower() not in _LOOPBACK_BINDS


def startup_security_issues(s: Settings) -> tuple[list[str], list[str]]:
    """Return (fatal, warnings) for the current configuration.

    Fatal issues abort startup; they are only raised when the GUI looks
    network-exposed (a non-loopback WEB_BIND), so a localhost-only run stays
    frictionless while an exposed one must be deliberately safe. This is the
    single source of truth for both the startup gate and its tests.
    """
    fatal: list[str] = []
    warnings: list[str] = []
    exposed = web_exposed(s)

    if not s.auth_enabled:
        # The built-in login is off; a reverse proxy is meant to enforce auth.
        if exposed and not s.auth_delegated_ack:
            fatal.append(
                f"AUTH_ENABLED=false exposes the Docker-controlling GUI on "
                f"WEB_BIND={s.web_bind!r} with NO built-in login. Put a reverse "
                f"proxy that authenticates every request in front of it and set "
                f"AUTH_DELEGATED_ACK=true to confirm — or set AUTH_ENABLED=true, "
                f"or bind the GUI to 127.0.0.1."
            )
        else:
            warnings.append(
                "AUTH_ENABLED=false — the built-in login is DISABLED. A reverse "
                "proxy in front MUST enforce authentication; the GUI controls Docker."
            )
    else:
        if not s.admin_username or not s.admin_password:
            # Usually a '$' in .env that Compose swallowed, leaving it empty (#140).
            msg = (
                "ADMIN_USERNAME or ADMIN_PASSWORD is empty — login cannot work. If "
                "the value contains a '$', write it twice ('$$') in .env: Docker "
                "Compose eats a single '$'."
            )
            (fatal if exposed else warnings).append(msg)
        elif s.admin_password == EXAMPLE_PASSWORD:
            base = "ADMIN_PASSWORD is still the example value — change it in .env."
            if exposed:
                fatal.append(
                    base + f" Refusing to start with it while the GUI is exposed "
                    f"(WEB_BIND={s.web_bind!r})."
                )
            else:
                warnings.append(base)
        elif len(s.admin_password) < 12:
            warnings.append(
                "ADMIN_PASSWORD is short (<12 chars) — use a strong password, "
                "especially before exposing the GUI beyond localhost."
            )

    if s.trusted_proxies_invalid:
        # Named explicitly: a typo'd entry silently shrinks the trust list, and
        # the symptom (throttling keyed on the proxy again) is hard to spot.
        warnings.append(
            "TRUSTED_PROXIES has unparseable entries, ignored: "
            + ", ".join(repr(e) for e in s.trusted_proxies_invalid)
        )
    if s.session_secret_generated:
        warnings.append(
            "SESSION_SECRET not set — generated a random one; all sessions reset on "
            "restart. Set SESSION_SECRET in .env for stable sessions."
        )
    return fatal, warnings


settings = Settings.from_env()
