import time

import pytest
from starlette.websockets import WebSocketDisconnect

from services.steam_service import DownloadJob, steam


def _fake_running_job(branch: str) -> DownloadJob:
    job = DownloadJob(branch=branch, started_at=time.time())
    steam.jobs[branch] = job
    return job


def teardown_function():
    steam.jobs.clear()


def test_status_requires_session(client):
    assert client.get("/api/serverfiles").status_code == 401


def test_status_lists_both_branches(logged_in):
    r = logged_in.get("/api/serverfiles")
    assert r.status_code == 200
    body = r.json()
    assert body["docker"] is False
    assert "steamcmd_image" in body and "server_image" in body
    branches = {b["branch"]: b for b in body["branches"]}
    assert branches["stable"]["app_id"] == "1874900"
    assert branches["experimental"]["app_id"] == "1890870"
    assert branches["experimental"]["installed"] is None


def test_download_unknown_branch_404(logged_in):
    assert logged_in.post("/api/serverfiles/nightly/download").status_code == 404


def test_check_update_docker_down_409(logged_in):
    # conftest mocks docker ping False -> check-update reports daemon down
    assert logged_in.get("/api/serverfiles/stable/check-update").status_code == 409


def test_check_update_unknown_branch_404(logged_in):
    assert logged_in.get("/api/serverfiles/nightly/check-update").status_code == 404


def test_remove_serverfiles_unknown_branch_404(logged_in):
    assert logged_in.delete("/api/serverfiles/nightly").status_code == 404


def test_remove_serverfiles_calls_steam(logged_in, monkeypatch):
    from services import instance_service, steam_service

    called = {}
    monkeypatch.setattr(
        instance_service, "running_instance_names_for_branch", lambda b: []
    )
    monkeypatch.setattr(
        steam_service.steam, "remove_files", lambda b: called.setdefault("branch", b)
    )
    assert logged_in.delete("/api/serverfiles/stable").status_code == 204
    assert called["branch"] == "stable"


def test_remove_serverfiles_blocked_when_running(logged_in, monkeypatch):
    from services import instance_service

    monkeypatch.setattr(
        instance_service, "running_instance_names_for_branch", lambda b: ["srv-a"]
    )
    r = logged_in.delete("/api/serverfiles/stable")
    assert r.status_code == 409
    assert "srv-a" in r.json()["detail"]


def test_version_endpoint(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] and body["repo_url"].startswith("https://github.com/")


def test_download_conflict_while_running(logged_in):
    _fake_running_job("stable")
    r = logged_in.post("/api/serverfiles/stable/download")
    assert r.status_code == 409


def test_websocket_rejects_anonymous(client):
    # The handshake is closed before accept, surfacing as a disconnect on connect
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/serverfiles/stable/ws"):
            pass
    assert exc.value.code == 4401


def test_websocket_snapshot_with_session(logged_in):
    job = _fake_running_job("stable")
    job.log.append("Update state (0x61) downloading, progress: 1.00 (1 / 100)")
    with logged_in.websocket_connect("/api/serverfiles/stable/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["state"]["branch"] == "stable"
        assert snap["state"]["job"]["status"] == "running"
        assert len(snap["log"]) == 1
