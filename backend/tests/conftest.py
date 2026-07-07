import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must be set before config/main are imported by test modules
os.environ["ADMIN_USERNAME"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "testpass-123"
os.environ["SESSION_SECRET"] = "unit-test-secret"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="rsm-test-")
os.environ["SERVERFILES_DIR"] = tempfile.mkdtemp(prefix="rsm-test-sf-")
os.environ["STATIC_DIR"] = ""

import config  # noqa: E402

config.settings = config.Settings.from_env()


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import auth
    import main
    from services import docker_service

    # Keep tests off the real Docker daemon
    monkeypatch.setattr(docker_service, "ping", lambda: False)
    monkeypatch.setattr(docker_service, "remove_exited", lambda role: None)
    monkeypatch.setattr(docker_service, "find_containers", lambda *a, **k: [])

    auth._attempts.clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def logged_in(client):
    client.post(
        "/api/auth/login", json={"username": "testadmin", "password": "testpass-123"}
    )
    return client
