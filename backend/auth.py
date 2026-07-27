"""Login, logout and signed-cookie session handling.

Credentials come from .env (ADMIN_USERNAME / ADMIN_PASSWORD). Sessions are
itsdangerous-signed cookies; no server-side session store is needed.
"""
import hmac
import ipaddress
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

import config

logger = logging.getLogger("manager.auth")

COOKIE_NAME = "rsm_session"

# Username attributed to requests when the built-in login is disabled
# (AUTH_ENABLED=false) and a reverse proxy is expected to enforce auth (#37).
ANONYMOUS_USER = "anonymous"

# In-memory brute-force throttle, keyed on the *real* client (see client_ip).
#
# Deliberately per-client and never global: a global cap is itself a denial of
# service, since one attacker could burn it and lock every legitimate operator
# out. Behind a reverse proxy every request shares the proxy's peer address, so
# without the X-Forwarded-For resolution below "per IP" silently collapsed into
# exactly that global bucket (security review R4).
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 10
# Lockout applied after each successive window breach; the last value repeats.
_LOCKOUT_STEPS = (60, 300, 900, 3600)
# How long a client's strike count survives inactivity, so escalation is not
# reset by simply waiting out the 60s window.
_STRIKE_MEMORY_SECONDS = 3600
# Hard bound on tracked clients, so a distributed attempt cannot grow the dict
# without limit between evictions.
_MAX_TRACKED_CLIENTS = 10_000


@dataclass
class _AttemptRecord:
    """One client's recent attempts, strike count and active lockout."""

    window: deque = field(default_factory=deque)
    strikes: int = 0
    locked_until: float = 0.0
    last_seen: float = 0.0


_attempts: dict[str, _AttemptRecord] = {}

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.settings.session_secret, salt="rsm-session")


def _as_ip(raw: str):
    """Parse an address, tolerating an IPv6 '[addr]' wrapper. None if invalid."""
    raw = (raw or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


def _is_trusted_proxy(addr) -> bool:
    return addr is not None and any(
        addr in net for net in config.settings.trusted_proxies
    )


def client_ip(request: Request) -> str:
    """The address to attribute this request to for throttling.

    X-Forwarded-For is believed ONLY when the direct peer is a configured
    trusted proxy; otherwise a client could set the header itself and either
    evade the throttle (a fresh identity per attempt) or lock somebody else out
    by naming their address. When the peer is trusted, the header is walked
    right-to-left and the first address that is not itself a trusted proxy is
    the real client — anything further left was supplied by the client and is
    ignored, which is what makes a spoofed prefix harmless.
    """
    peer = request.client.host if request.client else "unknown"
    if not config.settings.trusted_proxies:
        return peer
    if not _is_trusted_proxy(_as_ip(peer)):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    for entry in reversed(forwarded.split(",")):
        addr = _as_ip(entry)
        if addr is None:
            continue
        if not _is_trusted_proxy(addr):
            return str(addr)
    return peer


def _throttle(request: Request) -> None:
    """Rate-limit login attempts for one client; raise 429 when over the limit."""
    ip = client_ip(request)
    now = time.monotonic()
    _evict_stale_attempts(now)

    record = _attempts.get(ip)
    if record is None:
        if len(_attempts) >= _MAX_TRACKED_CLIENTS:
            _evict_oldest(now)
        record = _attempts[ip] = _AttemptRecord()
    record.last_seen = now

    if record.locked_until > now:
        raise HTTPException(
            status_code=429,
            detail=_lockout_message(record.locked_until - now),
        )

    while record.window and now - record.window[0] > _LOGIN_WINDOW_SECONDS:
        record.window.popleft()

    if len(record.window) >= _LOGIN_MAX_ATTEMPTS:
        record.strikes += 1
        penalty = _LOCKOUT_STEPS[min(record.strikes, len(_LOCKOUT_STEPS)) - 1]
        record.locked_until = now + penalty
        record.window.clear()
        logger.warning(
            "Login throttled for %s — %d attempts in %ds (strike %d, locked for %ds)",
            ip, _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECONDS, record.strikes, penalty,
        )
        raise HTTPException(status_code=429, detail=_lockout_message(penalty))

    record.window.append(now)


def _lockout_message(seconds: float) -> str:
    return f"Too many login attempts. Try again in {max(1, int(seconds))} seconds."


def note_login_success(request: Request) -> None:
    """Clear a client's throttle state after a successful login.

    Without this a legitimate operator who mistypes a few times carries the
    strikes — and the escalating lockout — into a session they already proved
    they are entitled to.
    """
    _attempts.pop(client_ip(request), None)


def _evict_stale_attempts(now: float) -> None:
    """Forget clients that are neither locked out nor recently active.

    Entries were only ever pruned when that same IP came back, so an
    internet-facing GUI accumulated one per source address, for ever (#88).
    A locked-out record is always kept: dropping it would hand back a clean
    slate, which is precisely what the lockout exists to deny.
    """
    stale = [
        ip for ip, r in _attempts.items()
        if r.locked_until <= now and now - r.last_seen > _STRIKE_MEMORY_SECONDS
    ]
    for ip in stale:
        del _attempts[ip]


def _evict_oldest(now: float) -> None:
    """Drop the least recently seen unlocked record to stay under the cap."""
    candidates = [ip for ip, r in _attempts.items() if r.locked_until <= now]
    if not candidates:
        return
    del _attempts[min(candidates, key=lambda ip: _attempts[ip].last_seen)]


def session_username(token: str | None) -> str | None:
    """Validate a session cookie value; also usable from WebSocket handshakes.

    When the built-in login is disabled (AUTH_ENABLED=false), every request is
    treated as the anonymous user — the reverse proxy in front is responsible
    for authentication. This single gate covers HTTP (require_session) and the
    WebSocket handshakes that call session_username directly.
    """
    if not config.settings.auth_enabled:
        return ANONYMOUS_USER
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=config.settings.session_ttl_hours * 3600)
    except (BadSignature, SignatureExpired):
        return None


def is_https(request) -> bool:
    """True when the browser's connection is HTTPS.

    The app always speaks plain HTTP inside its container, so a terminating TLS
    reverse proxy is recognised by its ``X-Forwarded-Proto``. Believing that
    header can only affect the caller's own request (a Secure cookie, an HSTS
    header) and never another user's, so it needs no trusted-proxy list.
    """
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded.split(",")[0].strip().lower() == "https":
        return True
    return getattr(getattr(request, "url", None), "scheme", "") == "https"


def _cookie_should_be_secure(request: Request) -> bool:
    """Whether to mark the session cookie ``Secure`` for this response.

    Explicit ``SESSION_COOKIE_SECURE=true`` always wins; otherwise it follows the
    actual connection, so a TLS deployment is protected without extra config and
    the plain-HTTP localhost default still works (security review R3).
    """
    return config.settings.session_cookie_secure or is_https(request)


def origin_allowed(origin: str, host: str) -> bool:
    """Is this browser Origin acceptable for a state-changing request?

    Same-origin is the normal case: the Origin's host:port must equal the Host
    the request was addressed to. Behind a reverse proxy both are the public
    name (proxies forward Host), so this needs no configuration. ALLOWED_ORIGINS
    covers the exception — a proxy that rewrites Host.
    """
    origin = (origin or "").strip().rstrip("/")
    if not origin:
        return True  # no Origin: a non-browser client (curl, scripts) — not CSRF
    if origin in config.settings.allowed_origins:
        return True
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc == (host or "").strip()


def request_origin_ok(request) -> bool:
    """Origin check for an HTTP request or a WebSocket handshake."""
    return origin_allowed(
        request.headers.get("origin", ""), request.headers.get("host", "")
    )


def require_session(request: Request) -> str:
    """FastAPI dependency: returns the logged-in username or raises 401."""
    username = session_username(request.cookies.get(COOKIE_NAME))
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    _throttle(request)
    cfg = config.settings
    if not cfg.admin_username or not cfg.admin_password:
        # Usually a '$' in .env that Docker Compose swallowed (a single '$' is a
        # variable reference; write it '$$'), which leaves the value empty (#140).
        raise HTTPException(
            status_code=503,
            detail=(
                "ADMIN_USERNAME/ADMIN_PASSWORD not configured. If your value contains "
                "a '$', write it twice ('$$') in .env — Docker Compose eats a single '$'."
            ),
        )
    user_ok = hmac.compare_digest(body.username.encode(), cfg.admin_username.encode())
    pass_ok = hmac.compare_digest(body.password.encode(), cfg.admin_password.encode())
    if not (user_ok and pass_ok):
        # Logged so a brute-force attempt is visible in the container log at all
        # — there was previously no record that a login had ever failed.
        logger.warning("Failed login attempt from %s", client_ip(request))
        raise HTTPException(status_code=401, detail="Invalid username or password")
    note_login_success(request)
    response.set_cookie(
        COOKIE_NAME,
        _serializer().dumps(cfg.admin_username),
        max_age=cfg.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        # Secure when TLS is actually in use: SESSION_COOKIE_SECURE=true forces
        # it, and a request arriving over HTTPS (directly, or via a proxy's
        # X-Forwarded-Proto) auto-enables it so a TLS deployment never leaks the
        # session on a downgrade — without breaking the plain-HTTP localhost
        # default, where a Secure cookie would simply never be sent (#88, R3).
        secure=_cookie_should_be_secure(request),
    )
    return {"username": cfg.admin_username}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(require_session)):
    return {"username": username}
