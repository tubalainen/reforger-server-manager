import time

import pytest


def test_me_requires_session(client):
    assert client.get("/api/auth/me").status_code == 401


def test_login_rejects_bad_credentials(client):
    r = client.post("/api/auth/login", json={"username": "testadmin", "password": "wrong"})
    assert r.status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_login_logout_roundtrip(client):
    r = client.post(
        "/api/auth/login", json={"username": "testadmin", "password": "testpass-123"}
    )
    assert r.status_code == 200
    assert r.json() == {"username": "testadmin"}

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "testadmin"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_tampered_cookie_rejected(client):
    client.post("/api/auth/login", json={"username": "testadmin", "password": "testpass-123"})
    client.cookies.set("rsm_session", "forged-token")
    assert client.get("/api/auth/me").status_code == 401


def test_login_reports_missing_credentials_with_dollar_hint(client, monkeypatch):
    import config

    # A '$' in .env that Docker Compose swallowed leaves the value empty; login
    # must say so clearly and point at the '$$' escaping fix (#140).
    monkeypatch.setattr(config.settings, "admin_username", "")
    r = client.post("/api/auth/login", json={"username": "whoever", "password": "whatever"})
    assert r.status_code == 503
    assert "$$" in r.json()["detail"]


def test_login_throttled_after_repeated_failures(client):
    for _ in range(10):
        r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 429


def test_health_is_public_and_minimal(client):
    # Liveness only: it used to leak APP_VERSION to unauthenticated callers (R6).
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version_reports_auth_enabled(client):
    assert client.get("/api/version").json()["auth_enabled"] is True


def test_version_hides_build_details_until_logged_in(client):
    # The SPA needs auth_enabled before login; the precise build is what an
    # attacker would match against known CVEs, so it needs a session (R6).
    anon = client.get("/api/version").json()
    assert "version" not in anon and "repo_url" not in anon

    client.post("/api/auth/login", json={"username": "testadmin", "password": "testpass-123"})
    known = client.get("/api/version").json()
    assert known["version"] and known["repo_url"]


def test_auth_disabled_bypasses_login(client, monkeypatch):
    import config

    # AUTH_ENABLED=false -> protected endpoints work with no session cookie,
    # every caller is the anonymous user (a reverse proxy owns auth) (#37).
    monkeypatch.setattr(config.settings, "auth_enabled", False)
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "anonymous"
    # a normally-protected data endpoint is reachable too
    assert client.get("/api/instances").status_code == 200
    assert client.get("/api/version").json()["auth_enabled"] is False


def test_login_attempt_windows_are_evicted(client):
    # The throttle kept one record per source IP for ever — pruned only if that same
    # IP came back. An internet-facing GUI accumulated them without limit (#88).
    import auth

    auth._attempts.clear()
    for i in range(5):
        client.post("/api/auth/login", json={"username": f"u{i}", "password": "nope"})
    assert len(auth._attempts) >= 1

    # Age every record past the strike-memory horizon; the next login sweeps them.
    for record in auth._attempts.values():
        record.last_seen -= auth._STRIKE_MEMORY_SECONDS * 2
    client.post("/api/auth/login", json={"username": "x", "password": "nope"})
    assert len(auth._attempts) == 1  # only the current caller remains


def test_session_cookie_is_secure_when_configured(client, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "session_cookie_secure", True)
    r = client.post(
        "/api/auth/login", json={"username": "testadmin", "password": "testpass-123"}
    )
    assert r.status_code == 200
    assert "secure" in r.headers["set-cookie"].lower()


def test_session_cookie_secure_auto_enabled_behind_tls_proxy(client):
    # A terminating TLS proxy forwards X-Forwarded-Proto: https; the cookie must
    # then be Secure even with SESSION_COOKIE_SECURE unset (security review R3).
    r = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "testpass-123"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200
    assert "secure" in r.headers["set-cookie"].lower()


def test_session_cookie_not_secure_on_plain_http(client):
    # The plain-HTTP localhost default must NOT set Secure, or the cookie would
    # never be sent back and login would appear broken.
    r = client.post(
        "/api/auth/login", json={"username": "testadmin", "password": "testpass-123"}
    )
    assert r.status_code == 200
    assert "secure" not in r.headers["set-cookie"].lower()


# --------------------------------------------------------------------------- #
# Client identification + brute-force throttle (security review R4)
# --------------------------------------------------------------------------- #

class _Req:
    """Minimal stand-in for a Request: the throttle only reads these two."""

    def __init__(self, peer, xff=None):
        self.client = type("_C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}


def _trust(monkeypatch, *cidrs):
    import config
    nets, _ = config._parse_networks(",".join(cidrs))
    monkeypatch.setattr(config.settings, "trusted_proxies", nets)


def test_client_ip_ignores_forwarded_header_without_trusted_proxies(monkeypatch):
    import auth
    _trust(monkeypatch)  # none configured: the header must be worthless
    assert auth.client_ip(_Req("203.0.113.9", xff="1.2.3.4")) == "203.0.113.9"


def test_client_ip_ignores_forwarded_header_from_untrusted_peer(monkeypatch):
    import auth
    _trust(monkeypatch, "172.31.242.0/24")
    # Peer is not a trusted proxy, so its X-Forwarded-For is a client-set lie.
    assert auth.client_ip(_Req("203.0.113.9", xff="1.2.3.4")) == "203.0.113.9"


def test_client_ip_uses_forwarded_header_from_trusted_proxy(monkeypatch):
    import auth
    _trust(monkeypatch, "172.31.242.0/24")
    assert auth.client_ip(_Req("172.31.242.5", xff="198.51.100.7")) == "198.51.100.7"


def test_client_ip_ignores_spoofed_prefix_through_trusted_proxy(monkeypatch):
    # A client sending its own XFF gets it prefixed; the proxy appends the address
    # it actually saw. The rightmost untrusted entry is the only believable one.
    import auth
    _trust(monkeypatch, "172.31.242.0/24")
    req = _Req("172.31.242.5", xff="9.9.9.9, 198.51.100.7")
    assert auth.client_ip(req) == "198.51.100.7"


def test_client_ip_walks_back_through_chained_proxies(monkeypatch):
    import auth
    _trust(monkeypatch, "172.31.242.0/24", "10.0.0.0/8")
    req = _Req("172.31.242.5", xff="198.51.100.7, 10.0.0.4")
    assert auth.client_ip(req) == "198.51.100.7"


def test_throttle_is_per_client_not_global(monkeypatch):
    """One attacker must NOT be able to lock everyone else out.

    This is the concrete failure R4 describes: behind a proxy every request
    shared one bucket, so exhausting it denied the login to all operators.
    """
    from fastapi import HTTPException

    import auth

    _trust(monkeypatch, "172.31.242.0/24")
    auth._attempts.clear()

    attacker = _Req("172.31.242.5", xff="198.51.100.7")
    for _ in range(auth._LOGIN_MAX_ATTEMPTS):
        auth._throttle(attacker)
    with pytest.raises(HTTPException) as exc:
        auth._throttle(attacker)
    assert exc.value.status_code == 429

    # A different real client behind the SAME proxy is unaffected.
    auth._throttle(_Req("172.31.242.5", xff="203.0.113.42"))


def test_throttle_lockout_escalates(monkeypatch):
    from fastapi import HTTPException

    import auth

    _trust(monkeypatch)
    auth._attempts.clear()
    req = _Req("198.51.100.7")

    def burn_through_a_window():
        record = auth._attempts.get("198.51.100.7")
        if record:  # skip an active lockout to reach the next breach
            record.locked_until = 0.0
        for _ in range(auth._LOGIN_MAX_ATTEMPTS):
            auth._throttle(req)
        with pytest.raises(HTTPException):
            auth._throttle(req)
        return auth._attempts["198.51.100.7"]

    first = burn_through_a_window().locked_until
    second = burn_through_a_window().locked_until
    # Second breach must buy a strictly longer penalty than the first.
    assert auth._attempts["198.51.100.7"].strikes == 2
    assert second > first


def test_successful_login_clears_throttle_state(client):
    # A legitimate operator who mistypes must not carry strikes into the session.
    import auth

    auth._attempts.clear()
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "testadmin", "password": "no"})
    assert auth._attempts

    r = client.post(
        "/api/auth/login", json={"username": "testadmin", "password": "testpass-123"}
    )
    assert r.status_code == 200
    assert auth._attempts == {}


def test_locked_out_record_is_never_evicted(monkeypatch):
    # Eviction must not hand back a clean slate mid-lockout.
    import auth

    _trust(monkeypatch)
    auth._attempts.clear()
    record = auth._AttemptRecord(strikes=3, locked_until=time.monotonic() + 600)
    record.last_seen = time.monotonic() - auth._STRIKE_MEMORY_SECONDS * 10
    auth._attempts["198.51.100.7"] = record

    auth._evict_stale_attempts(time.monotonic())
    assert "198.51.100.7" in auth._attempts


# --------------------------------------------------------------------------- #
# API surface + response hardening (security review R6, R7, R12)
# --------------------------------------------------------------------------- #

def test_interactive_docs_are_disabled(client):
    # /docs, /redoc and /openapi.json enumerate the whole Docker-controlling API
    # and are served before any auth dependency runs (R6).
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_security_headers_present_on_api_and_spa(client):
    r = client.get("/api/health")
    h = r.headers
    assert "default-src 'self'" in h["content-security-policy"]
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"


def test_csp_never_allows_inline_script(client):
    csp = client.get("/api/health").headers["content-security-policy"]
    script = [d for d in csp.split(";") if d.strip().startswith("script-src")][0]
    assert "unsafe-inline" not in script and "unsafe-eval" not in script


def test_hsts_only_over_https(client):
    assert "strict-transport-security" not in client.get("/api/health").headers
    r = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=" in r.headers["strict-transport-security"]


def test_cross_origin_state_change_is_blocked(logged_in):
    # The session is a cookie, so a write a browser says came from another site
    # must be refused even with a valid session (R7).
    r = logged_in.post(
        "/api/templates/locks/clear", headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert "Cross-origin" in r.json()["detail"]


def test_same_origin_state_change_is_allowed(logged_in):
    r = logged_in.post(
        "/api/templates/locks/clear",
        headers={"Origin": "http://testserver", "Host": "testserver"},
    )
    assert r.status_code == 200


def test_state_change_without_origin_still_works(logged_in):
    # curl / scripts send no Origin; only browsers do, and only they are at risk.
    assert logged_in.post("/api/templates/locks/clear").status_code == 200


def test_safe_methods_are_not_origin_checked(logged_in):
    r = logged_in.get("/api/instances", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200


def test_origin_allowed_matches_host_and_allowlist(monkeypatch):
    import auth
    import config

    assert auth.origin_allowed("http://testserver", "testserver")
    assert auth.origin_allowed("https://rsm.example.com", "rsm.example.com")
    assert not auth.origin_allowed("https://evil.example", "rsm.example.com")
    assert auth.origin_allowed("", "rsm.example.com")  # no Origin => not a browser
    monkeypatch.setattr(config.settings, "allowed_origins", ("https://proxy.example",))
    assert auth.origin_allowed("https://proxy.example", "somethingelse")


def test_websocket_rejects_foreign_origin(logged_in):
    # Cross-Site WebSocket Hijacking (R12): the handshake carries the cookie, so
    # Origin must be checked before the socket is accepted.
    import pytest as _pytest
    from starlette.websockets import WebSocketDisconnect

    with _pytest.raises(WebSocketDisconnect) as exc:
        with logged_in.websocket_connect(
            "/api/instances/1/logs", headers={"Origin": "https://evil.example"}
        ):
            pass
    assert exc.value.code == 4403


# --------------------------------------------------------------------------- #
# Session revocation (security review R13)
# --------------------------------------------------------------------------- #

def test_logout_all_invalidates_other_sessions(client, monkeypatch):
    from fastapi.testclient import TestClient

    import auth as auth_mod
    import main

    # Two independent "devices" signed in with the same account.
    a = client
    a.post("/api/auth/login", json={"username": "testadmin", "password": "testpass-123"})
    with TestClient(main.app) as b:
        b.post("/api/auth/login", json={"username": "testadmin", "password": "testpass-123"})
        assert a.get("/api/auth/me").status_code == 200
        assert b.get("/api/auth/me").status_code == 200

        # Rotating the salt must invalidate BOTH cookies, not just the caller's —
        # /logout only clears the caller's copy, which is the gap R13 describes.
        assert a.post("/api/auth/logout-all").status_code == 200
        assert a.get("/api/auth/me").status_code == 401
        assert b.get("/api/auth/me").status_code == 401

    auth_mod.rotate_session_salt()  # leave a clean salt for later tests


def test_logout_all_requires_a_session(client):
    assert client.post("/api/auth/logout-all").status_code == 401


def test_session_salt_persists_across_serializer_calls(client):
    import auth as auth_mod

    auth_mod.rotate_session_salt()
    first = auth_mod._session_salt()
    assert first and first != auth_mod._BASE_SALT
    assert auth_mod._session_salt() == first   # stable => sessions survive restarts
