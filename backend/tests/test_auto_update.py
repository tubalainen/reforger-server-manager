"""The daily server-files update check, its two toggles and their chain (#177)."""
import time

import pytest

from services import auto_update, instance_service
from services.steam_service import DownloadJob, steam


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_jobs():
    steam.jobs.clear()
    auto_update._download_tasks.clear()
    yield
    steam.jobs.clear()
    auto_update._download_tasks.clear()


def _wire(monkeypatch, *, installed, latest, docker=True):
    """Point the check at fake Steam answers. `installed` maps branch -> build id."""
    from services import docker_service

    monkeypatch.setattr(docker_service, "ping", lambda: docker)
    monkeypatch.setattr(auto_update.docker_service, "ping", lambda: docker)
    monkeypatch.setattr(
        steam, "installed_info",
        lambda b: {"build_id": installed[b]} if installed.get(b) else None,
    )
    asked = []
    monkeypatch.setattr(
        steam, "latest_build_id",
        lambda b: asked.append(b) or latest.get(b),
    )
    return asked


def _branch(status: dict, name: str) -> dict:
    return next(b for b in status["branches"] if b["branch"] == name)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

def test_defaults_are_off_and_a_check_is_due():
    status = auto_update.status()
    assert status["auto_download"] is False
    assert status["auto_restart"] is False
    assert status["last_check_at"] is None
    assert auto_update.due() is True


def test_settings_persist_and_none_leaves_a_toggle_alone():
    auto_update.save_settings(True, True)
    assert auto_update.status()["auto_download"] is True

    auto_update.save_settings(False, None)
    status = auto_update.status()
    assert status["auto_download"] is False
    assert status["auto_restart"] is True  # untouched by the None


def test_unparseable_stored_settings_read_as_defaults():
    auto_update._write({"auto_download": True})
    from sqlmodel import Session

    import models
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, auto_update.SETTINGS_KEY)
        row.value = "not json at all"
        session.add(row)
        session.commit()

    assert auto_update.status()["auto_download"] is False


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_check_records_the_new_build_without_downloading(monkeypatch):
    _wire(monkeypatch, installed={"stable": "100"}, latest={"stable": "200"})
    started = []
    monkeypatch.setattr(auto_update, "_start_download", lambda b: started.append(b))

    await auto_update._run_check()

    stable = _branch(auto_update.status(), "stable")
    assert stable["latest_build"] == "200"
    assert stable["update_available"] is True
    assert started == []  # auto_download is off: the prompt is all you get
    assert auto_update.due() is False  # ...and the daily clock was reset


@pytest.mark.anyio
async def test_up_to_date_branch_reports_no_update(monkeypatch):
    _wire(monkeypatch, installed={"stable": "100"}, latest={"stable": "100"})

    await auto_update._run_check()

    assert _branch(auto_update.status(), "stable")["update_available"] is False


@pytest.mark.anyio
async def test_uninstalled_branch_is_never_queried(monkeypatch):
    # Nothing on disk means there is no "update", only a first download — and
    # spawning a steamcmd container to learn that would be pure waste.
    asked = _wire(monkeypatch, installed={"stable": "100"}, latest={"stable": "100"})

    await auto_update._run_check()

    assert asked == ["stable"]
    experimental = _branch(auto_update.status(), "experimental")
    assert experimental["installed"] is False
    assert experimental["update_available"] is False


@pytest.mark.anyio
async def test_check_is_skipped_while_docker_is_down(monkeypatch):
    _wire(monkeypatch, installed={"stable": "100"}, latest={"stable": "200"}, docker=False)

    await auto_update._run_check()

    # Nothing recorded, and the check stays due so a later tick retries it.
    assert auto_update.status()["last_check_at"] is None
    assert auto_update.due() is True


@pytest.mark.anyio
async def test_steam_not_answering_is_reported_not_treated_as_an_update(monkeypatch):
    _wire(monkeypatch, installed={"stable": "100"}, latest={"stable": None})

    await auto_update._run_check()

    stable = _branch(auto_update.status(), "stable")
    assert stable["update_available"] is False
    assert stable["error"]


@pytest.mark.anyio
async def test_auto_download_starts_the_download_for_the_stale_branch(monkeypatch):
    auto_update.save_settings(True, False)
    _wire(
        monkeypatch,
        installed={"stable": "100", "experimental": "50"},
        latest={"stable": "200", "experimental": "50"},
    )
    started = []
    monkeypatch.setattr(auto_update, "_start_download", lambda b: started.append(b))

    await auto_update._run_check()

    assert started == ["stable"]  # experimental is current; it is left alone


@pytest.mark.anyio
async def test_a_toggle_flipped_during_a_check_is_not_written_back(monkeypatch):
    # Asking Steam can take a minute per branch; the settings are re-read when the
    # result is stored, so the user's click in that window survives.
    from services import docker_service

    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(auto_update.docker_service, "ping", lambda: True)
    monkeypatch.setattr(steam, "installed_info", lambda b: {"build_id": "100"})

    def slow_steam(branch):
        auto_update.save_settings(True, None)  # the user, mid-check
        return "200"

    monkeypatch.setattr(steam, "latest_build_id", slow_steam)
    monkeypatch.setattr(auto_update, "_start_download", lambda b: None)

    await auto_update._run_check()

    assert auto_update.status()["auto_download"] is True


@pytest.mark.anyio
async def test_tick_does_nothing_until_the_check_comes_due(monkeypatch):
    checks = []
    monkeypatch.setattr(auto_update, "start_check", lambda: checks.append(1))

    await auto_update.tick()
    assert checks == [1]  # never checked before -> due immediately

    auto_update._write({**auto_update._read(), "last_check_at": time.time()})
    await auto_update.tick()
    assert checks == [1]  # ...and not again for another day


# --------------------------------------------------------------------------- #
# Download -> restart
# --------------------------------------------------------------------------- #

def _finished_job(status: str = "success") -> DownloadJob:
    job = DownloadJob(branch="stable", started_at=time.time())
    job.finish(status, "" if status == "success" else "boom")
    return job


@pytest.mark.anyio
async def test_successful_auto_download_restarts_that_branchs_instances(monkeypatch):
    auto_update.save_settings(True, True)
    job = _finished_job()

    async def fake_start(branch):
        return job

    monkeypatch.setattr(steam, "start", fake_start)
    restarted = []
    monkeypatch.setattr(
        instance_service, "restart_instances_for_branch",
        lambda b: restarted.append(b) or ["srv-a"],
    )

    await auto_update._download_and_restart("stable")

    assert restarted == ["stable"]


@pytest.mark.anyio
async def test_auto_restart_off_leaves_running_servers_alone(monkeypatch):
    auto_update.save_settings(True, False)

    async def fake_start(branch):
        return _finished_job()

    monkeypatch.setattr(steam, "start", fake_start)
    restarted = []
    monkeypatch.setattr(
        instance_service, "restart_instances_for_branch", lambda b: restarted.append(b)
    )

    await auto_update._download_and_restart("stable")

    assert restarted == []


@pytest.mark.anyio
async def test_a_failed_download_never_restarts_anything(monkeypatch):
    auto_update.save_settings(True, True)

    async def fake_start(branch):
        return _finished_job("error")

    monkeypatch.setattr(steam, "start", fake_start)
    restarted = []
    monkeypatch.setattr(
        instance_service, "restart_instances_for_branch", lambda b: restarted.append(b)
    )

    await auto_update._download_and_restart("stable")

    assert restarted == []


@pytest.mark.anyio
async def test_download_already_running_is_not_started_twice(monkeypatch):
    auto_update.save_settings(True, True)

    async def refuse(branch):
        raise RuntimeError("A stable download is already running")

    monkeypatch.setattr(steam, "start", refuse)
    restarted = []
    monkeypatch.setattr(
        instance_service, "restart_instances_for_branch", lambda b: restarted.append(b)
    )

    await auto_update._download_and_restart("stable")  # must not raise

    assert restarted == []


# --------------------------------------------------------------------------- #
# instance_service.restart_instances_for_branch
# --------------------------------------------------------------------------- #

def _inst(**over):
    from models import Instance

    base = dict(id=1, name="srv", template_id=1, branch="stable",
                game_port=2001, a2s_port=17777, rcon_port=19999,
                desired_state="running")
    base.update(over)
    return Instance(**base)


def test_only_running_instances_of_that_branch_restart(monkeypatch):
    from services import docker_service

    instances = [
        _inst(id=1, name="a"),
        _inst(id=2, name="b", desired_state="stopped"),
        _inst(id=3, name="c", branch="experimental"),
    ]
    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service, "_all_instances", lambda _s: instances)
    done = []
    monkeypatch.setattr(instance_service, "restart_instance", lambda iid: done.append(iid))

    assert instance_service.restart_instances_for_branch("stable") == ["a"]
    assert done == [1]


def test_one_failing_restart_does_not_strand_the_others(monkeypatch):
    from services import docker_service

    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(
        instance_service, "_all_instances",
        lambda _s: [_inst(id=1, name="a"), _inst(id=2, name="b")],
    )

    def restart(iid):
        if iid == 1:
            raise instance_service.InstanceError("no server files")

    monkeypatch.setattr(instance_service, "restart_instance", restart)

    assert instance_service.restart_instances_for_branch("stable") == ["b"]


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

def test_auto_update_status_requires_a_session(client):
    assert client.get("/api/serverfiles/auto-update").status_code == 401


def test_api_reports_the_defaults(logged_in):
    body = logged_in.get("/api/serverfiles/auto-update").json()
    assert body["auto_download"] is False and body["auto_restart"] is False
    assert {b["branch"] for b in body["branches"]} == {"stable", "experimental"}


def test_api_saves_the_toggles(logged_in):
    body = logged_in.put(
        "/api/serverfiles/auto-update",
        json={"auto_download": True, "auto_restart": True},
    ).json()
    assert body["auto_download"] is True and body["auto_restart"] is True
    assert logged_in.get("/api/serverfiles/auto-update").json()["auto_restart"] is True


def test_check_now_needs_docker(logged_in):
    # conftest keeps the daemon down, and the check spawns a steamcmd container.
    assert logged_in.post("/api/serverfiles/auto-update/check").status_code == 409
