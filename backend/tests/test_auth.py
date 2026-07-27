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


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_version_reports_auth_enabled(client):
    assert client.get("/api/version").json()["auth_enabled"] is True


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
