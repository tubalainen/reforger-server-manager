"""All runtime configuration, read from environment (.env via docker compose)."""
import ipaddress
import os
import re
import secrets
from dataclasses import dataclass, field

import stacks

APP_NAME = "Reforger Server Manager"
APP_VERSION = "0.91.0"

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


_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9])?$")


def _parse_proxies(raw: str | None) -> tuple[tuple, tuple[str, ...], tuple[str, ...]]:
    """Parse 'IP,CIDR,NAME,...' into (networks, host names, malformed entries).

    A bare address is accepted as a single-host network. A name — `caddy`,
    `rsm-edge` — is the proxy's container, looked up when a request arrives, so
    exactly its address is trusted and not the network around it (security
    review 2026-10-20, F2). Malformed entries are returned rather than raised so
    startup can warn about them by name instead of silently trusting a shorter
    list than the operator intended.
    """
    networks = []
    names = []
    invalid = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            if _HOSTNAME_RE.fullmatch(part) and not part.replace(".", "").isdigit():
                names.append(part.lower())
            else:
                invalid.append(part)
    return tuple(networks), tuple(names), tuple(invalid)


def _host_of(raw: str) -> str:
    """The host name of a Host header or a site address, lower case, no port.

    'reforger.example.com:443' -> 'reforger.example.com', '[::1]:7780' -> '::1',
    'https://x.example' -> 'x.example', ':80' -> ''.
    """
    raw = (raw or "").strip().lower()
    for scheme in ("http://", "https://"):
        raw = raw.removeprefix(scheme)
    raw = raw.split("/", 1)[0]
    if raw.startswith("["):
        return raw[1:].split("]", 1)[0]
    if raw.count(":") > 1:
        return raw  # a bare IPv6 address
    return raw.split(":", 1)[0].rstrip(".")


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
    # Proxies named by their container (`caddy`, `rsm-edge`): their address is
    # looked up, so exactly that one is trusted (auth.client_ip).
    trusted_proxy_hosts: tuple[str, ...] = field(default=())
    trusted_proxies_invalid: tuple[str, ...] = field(default=())
    # Host names the GUI answers to while the built-in login is off, besides
    # localhost, any IP address and SITE_ADDRESS (security review 2026-10-20,
    # F4): with no login, a page on another name is a DNS-rebinding attack.
    allowed_hosts: tuple[str, ...] = field(default=())
    # SITE_ADDRESS's host name (the VPS Caddy and the edge serve the GUI there).
    site_host: str = ""
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
    # Which stack this manager is (#204): the name every container, network and
    # volume of this install starts with, and the label its containers carry.
    # 'reforger' — the default — keeps every name an install had before stacks.
    rsm_stack: str = stacks.DEFAULT_STACK
    # Set for the Server Supervisor; a manager derives its own (auth.cookie_name).
    session_cookie_name: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("SESSION_SECRET", "").strip()
        generated = not secret
        if generated:
            secret = secrets.token_hex(32)
        trusted, trusted_names, trusted_bad = _parse_proxies(os.environ.get("TRUSTED_PROXIES"))
        allowed_hosts = tuple(
            h for h in (_host_of(p) for p in os.environ.get("ALLOWED_HOSTS", "").split(",")) if h
        )
        origins = tuple(
            o.strip().rstrip("/")
            for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        )
        stack = os.environ.get("RSM_STACK", "").strip() or stacks.DEFAULT_STACK
        return cls(
            rsm_stack=stack,
            session_cookie_name=os.environ.get("SESSION_COOKIE_NAME", "").strip(),
            trusted_proxies=trusted,
            trusted_proxy_hosts=trusted_names,
            trusted_proxies_invalid=trusted_bad,
            allowed_hosts=allowed_hosts,
            site_host=_host_of(os.environ.get("SITE_ADDRESS", "")),
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
            # The compose file passes <stack>-net; the fallback says the same.
            docker_network=os.environ.get("DOCKER_NETWORK", "").strip() or f"{stack}-net",
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


def startup_security_issues(
    s: Settings, gui_password_set: bool = False
) -> tuple[list[str], list[str]]:
    """Return (fatal, warnings) for the current configuration.

    Fatal issues abort startup. Password issues are only fatal when the GUI looks
    network-exposed (a non-loopback WEB_BIND), so a localhost-only run stays
    frictionless while an exposed one must be deliberately safe; a login that is
    switched off needs AUTH_DELEGATED_ACK wherever the GUI is bound. This is the
    single source of truth for both the startup gate and its tests.

    `gui_password_set`: the password was changed in the GUI (#204), so it — not
    ADMIN_PASSWORD — is the one in effect, and it is judged instead: it was
    held to the minimum length when it was set, so there is nothing to object to.
    """
    fatal: list[str] = []
    warnings: list[str] = []
    exposed = web_exposed(s)

    if not s.auth_enabled:
        # The built-in login is off; a reverse proxy is meant to enforce auth.
        # Required on a loopback bind too (security review 2026-10-20, F4):
        # 127.0.0.1 is not just this machine's browser — every program on the
        # host, and every game server on host networking (Workshop mods
        # included), reaches it without passing the proxy.
        if not s.auth_delegated_ack:
            where = f"on WEB_BIND={s.web_bind!r}" if exposed else "on this machine"
            fatal.append(
                f"AUTH_ENABLED=false opens the Docker-controlling GUI {where} with "
                f"NO built-in login — to every program on this host and every game "
                f"server on host networking, not only to the proxy. Put a reverse "
                f"proxy that authenticates every request in front of it and set "
                f"AUTH_DELEGATED_ACK=true to confirm, or set AUTH_ENABLED=true."
            )
        else:
            warnings.append(
                "AUTH_ENABLED=false — the built-in login is DISABLED. A reverse "
                "proxy in front MUST enforce authentication; the GUI controls Docker. "
                "Programs on this host and game servers on host networking reach "
                "the GUI without the proxy."
            )
    elif gui_password_set:
        if not s.admin_username:
            msg = (
                "ADMIN_USERNAME is empty — login cannot work. If the value contains "
                "a '$', write it twice ('$$') in .env: Docker Compose eats a single '$'."
            )
            (fatal if exposed else warnings).append(msg)
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
            base = ("ADMIN_PASSWORD is still the example value — change it in .env, "
                    "or sign in and set one under System › Account.")
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

    wide = [str(n) for n in s.trusted_proxies if n.num_addresses > 1]
    if wide:
        # A Docker network's gateway is the host itself, inside the network's own
        # range: everything on the host would be a "proxy" that may name its own
        # client address, and the login throttle would stop no one (F2).
        warnings.append(
            "TRUSTED_PROXIES names a whole network (" + ", ".join(wide) + "). If it "
            "is a Docker network, everything on this host — game servers on host "
            "networking included — reaches the GUI from that network's gateway and "
            "can choose its own X-Forwarded-For, which defeats the login throttle. "
            "Name the proxy itself instead (TRUSTED_PROXIES=caddy, or rsm-edge "
            "behind the shared edge): the compose files of v0.81.3 do."
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
