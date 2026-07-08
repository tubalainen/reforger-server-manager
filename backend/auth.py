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
        raise HTTPException(status_code=503, detail="ADMIN_USERNAME/ADMIN_PASSWORD not configured")
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
    )
    return {"username": cfg.admin_username}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
async def me(username: str = Depends(require_session)):
    return {"username": username}
