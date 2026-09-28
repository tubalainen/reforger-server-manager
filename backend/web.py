"""What the manager and the Server Supervisor serve the same way (#204).

Both are a JSON API plus a built Vue page behind one login, so both harden their
responses the same way and hand deep links to the page's own router.
"""
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import auth
import config

# Kept tight on purpose: the SPA loads no third-party scripts, fonts or images,
# so nothing here needs a CDN allowance. 'unsafe-inline' is granted for STYLES
# only — Vue writes inline style attributes for :style bindings — and never for
# scripts, which is the direction that matters for XSS. Override with
# CONTENT_SECURITY_POLICY if a deployment genuinely needs a looser policy.
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'"
)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def harden(app: FastAPI, logger: logging.Logger) -> None:
    """Reject cross-site state changes, then harden every response (R7)."""

    @app.middleware("http")
    async def security_middleware(request, call_next):
        # CSRF: the session is a cookie, so a state-changing request that a browser
        # says came from another site must not be honoured. A request with no Origin
        # is a non-browser client (curl, scripts) and is left alone — browsers always
        # send Origin on cross-origin writes, which is what this is defending against.
        if request.method not in _SAFE_METHODS and not auth.request_origin_ok(request):
            logger.warning(
                "Blocked cross-origin %s %s from origin %r",
                request.method, request.url.path, request.headers.get("origin", ""),
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin request blocked"},
            )

        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy",
                           config.settings.content_security_policy or DEFAULT_CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")  # legacy peer of frame-ancestors
        headers.setdefault("Referrer-Policy", "no-referrer")
        if auth.is_https(request):
            # Only meaningful over TLS, and actively harmful on the plain-HTTP
            # localhost default: it would pin a browser to HTTPS for a host that
            # does not serve it.
            headers.setdefault("Strict-Transport-Security",
                               "max-age=31536000; includeSubDomains")
        return response


def serve_spa(app: FastAPI, static_dir: str, index: str = "index.html") -> None:
    """Serve the built frontend, if there is one (i.e. in the image).

    Files are served as they are; any other path that is not the API gets
    `index`, so deep links reach the page's own router.
    """
    root = Path(static_dir) if static_dir else None
    if not (root and root.is_dir()):
        return
    app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and root.resolve() in candidate.parents:
            return FileResponse(candidate)
        # Deep links (/servers, /library, ...) fall through to the SPA router
        return FileResponse(root / index)
