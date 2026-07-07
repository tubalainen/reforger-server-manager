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
