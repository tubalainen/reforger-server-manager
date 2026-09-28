"""Login, logout and signed-cookie session handling.

The username comes from .env (ADMIN_USERNAME). The password is ADMIN_PASSWORD
from .env until someone changes it in the GUI; from then on a scrypt hash in
this manager's database is the password, and .env no longer is (#204: team
admins have only the GUI, never the .env file). Sessions are
itsdangerous-signed cookies; no server-side session store is needed.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlmodel import Session

import config
import models
import stacks

logger = logging.getLogger("manager.auth")

COOKIE_NAME = "rsm_session"


def cookie_name() -> str:
    """This install's session cookie.

    Browsers keep cookies per host name, not per port. Every install reached by
    the same name — several stacks on one machine (#204), or a stack and the
    Server Supervisor — would overwrite the others' session cookie and log them
    out on every sign-in. The default stack keeps the name it always had, so an
    upgrade logs nobody out.
    """
    s = config.settings
    if s.session_cookie_name:
        return s.session_cookie_name
    return COOKIE_NAME if s.rsm_stack == stacks.DEFAULT_STACK else f"{COOKIE_NAME}_{s.rsm_stack}"

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


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


# --------------------------------------------------------------------------- #
# The password in effect: .env, until one is set in the GUI (#204)
# --------------------------------------------------------------------------- #
MIN_PASSWORD_LENGTH = 12
_PASSWORD_KEY = "admin_password"
# scrypt from the standard library: ~16 MB and a few tens of milliseconds per
# check — cheap for one login, expensive for anyone holding a stolen hash.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(bytes.fromhex(digest)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def _stored_password() -> dict:
    """The GUI-set password record ({"hash", "changed_at"}), or {} when unset."""
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, _PASSWORD_KEY)
    try:
        record = json.loads(row.value) if row else {}
    except ValueError:
        return {}
    return record if isinstance(record, dict) and record.get("hash") else {}


def gui_password_set() -> bool:
    """Has the password been changed in the GUI (so .env's no longer applies)?"""
    return bool(_stored_password())


def password_matches(candidate: str) -> bool:
    """Is this the password currently in effect?"""
    stored = _stored_password()
    if stored:
        return verify_password(candidate, stored["hash"])
    expected = config.settings.admin_password
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


def set_gui_password(password: str) -> None:
    now = datetime.now(UTC)
    record = json.dumps({"hash": hash_password(password), "changed_at": now.isoformat()})
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, _PASSWORD_KEY) or models.AppSetting(
            key=_PASSWORD_KEY)
        row.value = record
        row.updated_at = now
        session.add(row)
        session.commit()


def clear_gui_password() -> bool:
    """Forget the GUI-set password, so .env's applies again. True if one was set."""
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, _PASSWORD_KEY)
        if row is None:
            return False
        session.delete(row)
        session.commit()
    return True


_SALT_FILE = "session_salt"
_BASE_SALT = "rsm-session"


def _salt_path() -> Path:
    return Path(config.settings.data_dir) / _SALT_FILE


def _session_salt() -> str:
    """The current signing salt, persisted so sessions survive a restart.

    Kept in DATA_DIR rather than memory: a salt that changed on every boot would
    log everyone out on each update, which is exactly the annoyance
    SESSION_SECRET exists to avoid. Missing or unreadable falls back to the
    original constant, so an old install keeps its sessions on upgrade.
    """
    try:
        stored = _salt_path().read_text(encoding="utf-8").strip()
        return stored or _BASE_SALT
    except OSError:
        return _BASE_SALT


def rotate_session_salt() -> None:
    """Write a fresh salt, invalidating every token signed with the old one."""
    path = _salt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secrets.token_hex(16), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # e.g. a Windows bind mount
        pass


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.settings.session_secret, salt=_session_salt())


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
    username = session_username(request.cookies.get(cookie_name()))
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


def _issue_session(request: Request, response: Response) -> None:
    cfg = config.settings
    response.set_cookie(
        cookie_name(),
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


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    _throttle(request)
    cfg = config.settings
    gui_password = await asyncio.to_thread(gui_password_set)
    if not cfg.admin_username or not (cfg.admin_password or gui_password):
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
    pass_ok = await asyncio.to_thread(password_matches, body.password)
    if not (user_ok and pass_ok):
        # Logged so a brute-force attempt is visible in the container log at all
        # — there was previously no record that a login had ever failed.
        logger.warning("Failed login attempt from %s", client_ip(request))
        raise HTTPException(status_code=401, detail="Invalid username or password")
    note_login_success(request)
    _issue_session(request, response)
    return {"username": cfg.admin_username}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(cookie_name())
    return {"ok": True}


@router.post("/logout-all")
async def logout_all(response: Response, _user: str = Depends(require_session)):
    """Invalidate every existing session, on every device (security review R13).

    Sessions are stateless signed cookies, so /logout only clears the caller's
    own copy — a stolen cookie stays valid until it expires. Rotating the salt
    changes the key every token was signed with, so all of them stop verifying
    at once. That is the missing "I think my session leaked" button; previously
    the only remedy was changing SESSION_SECRET and restarting.
    """
    rotate_session_salt()
    response.delete_cookie(cookie_name())
    logger.warning("All sessions invalidated by an explicit logout-all request")
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(require_session)):
    return {"username": username}


@router.get("/password")
async def password_info(_user: str = Depends(require_session)):
    """Where the password in effect comes from, for the Account page."""
    stored = await asyncio.to_thread(_stored_password)
    return {
        "auth_enabled": config.settings.auth_enabled,
        "username": config.settings.admin_username,
        "source": "gui" if stored else "env",
        "changed_at": stored.get("changed_at"),
        "min_length": MIN_PASSWORD_LENGTH,
    }


@router.post("/password")
async def change_password(body: PasswordChange, request: Request, response: Response,
                          _user: str = Depends(require_session)):
    """Change the login password from the GUI (#204).

    Team admins have only the GUI — the .env file is on a machine they have no
    shell on. The new password is stored hashed in this manager's database and
    replaces .env's from then on. It needs the current password, so a session
    left open on someone else's screen is not enough; and it logs out every
    other session, so whoever had the old password is out.
    """
    if not config.settings.auth_enabled:
        raise HTTPException(
            status_code=400,
            detail="The built-in login is off (AUTH_ENABLED=false): your reverse "
                   "proxy handles the password, not this manager.",
        )
    # Same throttle as the login: an open session must not become an unlimited
    # way to guess the password.
    _throttle(request)
    if not await asyncio.to_thread(password_matches, body.current_password):
        logger.warning("Wrong current password on a password change from %s",
                       client_ip(request))
        raise HTTPException(status_code=400, detail="The current password is not right.")
    new = body.new_password
    if len(new) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Use at least {MIN_PASSWORD_LENGTH} characters.",
        )
    if new == body.current_password:
        raise HTTPException(status_code=400, detail="That is the current password.")
    note_login_success(request)
    await asyncio.to_thread(set_gui_password, new)
    # Every token signed so far stops verifying; this browser gets a fresh one.
    rotate_session_salt()
    _issue_session(request, response)
    logger.warning("The login password was changed in the GUI; every other session "
                   "was logged out")
    return {"ok": True}
