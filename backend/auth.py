"""Login, logout and signed-cookie session handling.

Credentials come from .env (ADMIN_USERNAME / ADMIN_PASSWORD). Sessions are
itsdangerous-signed cookies; no server-side session store is needed.
"""
import hmac
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

import config

COOKIE_NAME = "rsm_session"

# Username attributed to requests when the built-in login is disabled
# (AUTH_ENABLED=false) and a reverse proxy is expected to enforce auth (#37).
ANONYMOUS_USER = "anonymous"

# Naive in-memory brute-force throttle: max N login attempts per IP per window.
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 10
_attempts: dict[str, deque] = defaultdict(deque)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.settings.session_secret, salt="rsm-session")


def _throttle(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _attempts[ip]
    while window and now - window[0] > _LOGIN_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts, try again later")
    window.append(now)
    _evict_stale_attempts(now)


def _evict_stale_attempts(now: float) -> None:
    """Forget IPs whose window has expired.

    Entries were only ever pruned when that same IP came back, so an
    internet-facing GUI accumulated one deque per source address, for ever (#88).
    """
    stale = [
        ip for ip, window in _attempts.items()
        if not window or now - window[-1] > _LOGIN_WINDOW_SECONDS
    ]
    for ip in stale:
        del _attempts[ip]


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


def _cookie_should_be_secure(request: Request) -> bool:
    """Whether to mark the session cookie ``Secure`` for this response.

    Explicit ``SESSION_COOKIE_SECURE=true`` always wins. Otherwise honour a
    terminating TLS reverse proxy's ``X-Forwarded-Proto`` (the app itself always
    speaks plain HTTP inside its container), so a proper HTTPS deployment gets a
    Secure cookie without extra configuration (security review R3). Trusting this
    header can only downgrade an attacker's own session — never another user's —
    so it needs no trusted-proxy list here.
    """
    if config.settings.session_cookie_secure:
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    proto = forwarded.split(",")[0].strip().lower()
    return proto == "https" or request.url.scheme == "https"


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
        raise HTTPException(status_code=401, detail="Invalid username or password")
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
