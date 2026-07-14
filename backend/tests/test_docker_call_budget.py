"""The status endpoints must not re-query Docker once per instance (#87).

These tests count daemon round-trips. They are the regression guard for the
amplification the review found: instance_view used to ping() per instance, and the
list/summary/stats paths each looked the same container up two or three times.
"""
import time

import pytest
from sqlmodel import Session

import models
from services import docker_service, instance_service


class _FakeContainer:
    def __init__(self, instance_id, status="running", log=b""):
        self.id = f"cid-{instance_id}"
        self.status = status
        self.labels = {docker_service.LABEL_INSTANCE_ID: str(instance_id)}
        self.attrs = {"State": {"StartedAt": "2026-07-14T10:00:00Z", "Status": status}}
        self._log = log
        self.stats_calls = 0

    def logs(self, **_kw):
        return self._log

    def stats(self, stream=False):
        self.stats_calls += 1
        time.sleep(0.2)  # docker needs two CPU samples; this is the cost we moved off
        return {
            "cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000, "online_cpus": 2},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
            "memory_stats": {"usage": 1000, "limit": 2000},
        }


@pytest.fixture(autouse=True)
def _clean_cpu_sampler():
    # The sampler is module state with a background thread: without this, one test's
    # in-flight refresh lands in the next test's cache.
    instance_service._cpu_cache.clear()
    instance_service._cpu_sampling.clear()
    yield
    instance_service._cpu_cache.clear()
    instance_service._cpu_sampling.clear()


@pytest.fixture()
def three_instances(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    with Session(models.get_engine()) as session:
        for i in (1, 2, 3):
            session.add(models.Instance(
                id=i, name=f"srv{i}", template_id=1, branch="stable",
                game_port=2000 + i, a2s_port=17770 + i, rcon_port=19990 + i,
            ))
        session.commit()

    calls = {"ping": 0, "list": 0, "find": 0}
    containers = {i: _FakeContainer(i) for i in (1, 2, 3)}

    monkeypatch.setattr(docker_service, "ping", lambda: calls.__setitem__("ping", calls["ping"] + 1) or True)
    monkeypatch.setattr(
        docker_service, "instance_containers",
        lambda: (calls.__setitem__("list", calls["list"] + 1), containers)[1],
    )
    monkeypatch.setattr(
        docker_service, "find_instance_container",
        lambda iid: (calls.__setitem__("find", calls["find"] + 1), containers.get(iid))[1],
    )
    return calls, containers


def test_listing_instances_costs_one_ping_and_one_listing(three_instances):
    calls, _ = three_instances
    views = instance_service.list_views()

    assert len(views) == 3
    # Not one ping and one lookup PER INSTANCE, which is what it used to be.
    assert calls["ping"] == 1
    assert calls["list"] == 1
    assert calls["find"] == 0


def test_summary_costs_one_ping_and_one_listing(three_instances):
    calls, _ = three_instances
    summary = instance_service.instances_summary()

    assert summary["total"] == 3
    assert calls["ping"] == 1
    assert calls["list"] == 1
    # ...and it does NOT look each container up a second time to read its log.
    assert calls["find"] == 0


def test_stats_looks_the_container_up_once(three_instances):
    calls, _ = three_instances
    instance_service.instance_stats(1)
    assert calls["find"] == 1  # was: status lookup + a second lookup + a reload()


def test_stats_does_not_block_on_the_slow_docker_stats_call(three_instances):
    _, containers = three_instances
    instance_service._cpu_cache.clear()

    started = time.monotonic()
    stats = instance_service.instance_stats(1)
    elapsed = time.monotonic() - started

    # docker's stats endpoint waits for two CPU samples (~1-2s in reality; 0.2s in
    # the fake). The request must not wait for it — it serves the last sample and
    # refreshes in the background.
    assert elapsed < 0.15, f"instance_stats blocked for {elapsed:.2f}s on docker stats"
    assert stats["cpu_percent"] is None  # nothing sampled yet; the GUI shows "—"

    # the sample lands shortly after, and the next call serves it
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and 1 not in instance_service._cpu_cache:
        time.sleep(0.02)
    assert instance_service.instance_stats(1)["cpu_percent"] is not None
    assert containers[1].stats_calls == 1
