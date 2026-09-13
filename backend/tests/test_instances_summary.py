"""The Servers overview reads everything from one summary call (#189)."""
import time
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

import models
from services import docker_service, fleet_history, instance_service

STATS_LINE = b"FPS: 59.9, frame time (avg: 16.7 ms), Mem: 1190747 kB, Player: 12, AI: 40\n"


class _FakeContainer:
    def __init__(self, instance_id, started="2026-07-14T10:00:00Z", log=STATS_LINE):
        self.id = f"cid-{instance_id}"
        self.status = "running"
        self.labels = {docker_service.LABEL_INSTANCE_ID: str(instance_id)}
        self.attrs = {"State": {"StartedAt": started, "Status": "running"}}
        self._log = log

    def logs(self, **_kw):
        return self._log

    def stats(self, stream=False):
        return {
            "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
            "memory_stats": {"usage": 1000, "limit": 2000},
        }


@pytest.fixture(autouse=True)
def _clean_module_state():
    instance_service._cpu_cache.clear()
    instance_service._cpu_sampling.clear()
    instance_service._online_runs.clear()
    fleet_history.clear()
    yield
    instance_service._cpu_cache.clear()
    instance_service._cpu_sampling.clear()
    fleet_history.clear()


@pytest.fixture()
def fleet(monkeypatch, tmp_path):
    """Template 1 edited after server 1 started; server 1 running, server 2 stopped."""
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "public_address", "203.0.113.24")
    with Session(models.get_engine()) as session:
        session.add(models.Template(
            id=1, name="Conflict Everon", scenario_name="Conflict – Everon",
            config_json='{"game": {"maxPlayers": 64}}',
            updated_at=datetime(2026, 7, 14, 11, 0, 0),  # after StartedAt 10:00 UTC
        ))
        for i, branch in ((1, "stable"), (2, "experimental")):
            session.add(models.Instance(
                id=i, name=f"srv{i}", template_id=1, branch=branch,
                game_port=2000 + i, a2s_port=17770 + i, rcon_port=19990 + i,
                restart_schedule_json='{"times": ["04:00"]}' if i == 1 else "",
            ))
        session.commit()

    containers = {1: _FakeContainer(1)}
    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(docker_service, "instance_containers", lambda: containers)
    monkeypatch.setattr(
        docker_service, "find_instance_container", lambda iid: containers.get(iid)
    )
    monkeypatch.setattr(instance_service, "server_files_ready", lambda branch: branch == "stable")
    return containers


def _wait_for_cpu_sample(instance_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and instance_id not in instance_service._cpu_cache:
        time.sleep(0.02)


def test_a_running_server_carries_what_the_overview_table_shows(fleet):
    instance_service.instances_summary()  # primes the background CPU sampler
    _wait_for_cpu_sample(1)
    summary = instance_service.instances_summary()
    srv = next(s for s in summary["servers"] if s["id"] == 1)

    assert srv["status"] == "running"
    assert srv["server_state"] == "online"
    assert srv["players"] == 12
    assert srv["server_fps"] == 59.9
    assert srv["max_players"] == 64
    assert srv["template_name"] == "Conflict Everon"
    assert srv["scenario_name"] == "Conflict – Everon"
    assert srv["server_files_ready"] is True
    assert srv["next_restart"] and srv["next_restart"].endswith("04:00")
    assert srv["uptime_seconds"] > 0
    assert srv["connect"] == "203.0.113.24:2001"
    assert srv["cpu_percent"] == 10.0
    assert srv["mem_bytes"] == 1000


def test_template_edited_after_start_is_flagged(fleet):
    srv = next(s for s in instance_service.instances_summary()["servers"] if s["id"] == 1)
    assert srv["template_changed"] is True


def test_template_edited_before_start_is_not_flagged(fleet):
    fleet[1].attrs["State"]["StartedAt"] = datetime(2026, 7, 14, 12, 0, tzinfo=UTC).isoformat()
    srv = next(s for s in instance_service.instances_summary()["servers"] if s["id"] == 1)
    assert srv["template_changed"] is False


def test_a_stopped_server_has_no_live_numbers(fleet):
    srv = next(s for s in instance_service.instances_summary()["servers"] if s["id"] == 2)
    assert srv["status"] != "running"
    for key in ("players", "server_fps", "uptime_seconds", "cpu_percent", "mem_bytes"):
        assert srv[key] is None
    assert srv["template_changed"] is False
    # Its files are checked for its own branch, not borrowed from another server's.
    assert srv["server_files_ready"] is False


def test_totals_are_none_until_anything_was_sampled(fleet):
    summary = instance_service.instances_summary()
    # The very first call only starts the sampler; "—" beats a made-up 0%.
    assert summary["cpu_percent"] is None
    assert summary["mem_bytes"] is None


def test_totals_add_up_the_running_servers(fleet):
    fleet[3] = _FakeContainer(3)
    with Session(models.get_engine()) as session:
        session.add(models.Instance(
            id=3, name="srv3", template_id=1, branch="stable",
            game_port=2003, a2s_port=17773, rcon_port=19993,
        ))
        session.commit()
    instance_service.instances_summary()
    _wait_for_cpu_sample(1)
    _wait_for_cpu_sample(3)
    summary = instance_service.instances_summary()
    assert summary["running"] == 2
    assert summary["players_total"] == 24
    assert summary["cpu_percent"] == 20.0
    assert summary["mem_bytes"] == 2000


def test_summary_serves_the_recorded_history(fleet):
    instance_service.record_summary_sample()
    instance_service.record_summary_sample()
    history = instance_service.instances_summary()["history"]
    assert len(history) == 2
    assert history[-1]["running"] == 1
    assert history[-1]["players"] == 12


def test_history_keeps_only_the_last_hour_of_points():
    for n in range(fleet_history.HISTORY_POINTS + 5):
        fleet_history.record({"running": n, "players_total": 0}, now=1000 + n * 60)
    pts = fleet_history.points()
    assert len(pts) == fleet_history.HISTORY_POINTS
    # oldest first, and the oldest five were dropped
    assert pts[0]["running"] == 5
    assert pts[-1]["t"] == 1000 + (fleet_history.HISTORY_POINTS + 4) * 60
    assert pts[-1]["cpu_percent"] is None
