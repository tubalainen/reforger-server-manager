"""Functional review, second batch (v0.95.0): the freeze watchdog, the stop
timeout, log and resource limits, restart options, the scheduled backup, and
the Workshop's health."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlmodel import Session, select

import config
from gate import policy
from gate.policy import Denied
from models import Instance, get_engine
from services import instance_backup, instance_service
from services.instance_service import InstanceError
from services.template_service import TemplateSpec
from services.workshop_service import UNHEALTHY_AFTER, WorkshopService


def _inst(**over):
    base = dict(id=1, name="Alpha", template_id=1, branch="stable",
                game_port=2001, a2s_port=17777, rcon_port=19999, desired_state="running")
    base.update(over)
    return Instance(**base)


# --------------------------------------------------------------------------- #
# A frozen server
# --------------------------------------------------------------------------- #

class _Container:
    def __init__(self, params="-logStats 10000"):
        self.id = "c1"
        self.attrs = {"Config": {"Env": [f"ARMA_PARAMS={params}"]},
                      "State": {"StartedAt": "2026-11-11T00:00:00Z"}}


NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)


def _lines(*ages):
    """One status line per age (seconds before NOW), oldest first."""
    return [(NOW - timedelta(seconds=a), "FPS: 60.0, frame time, Mem: 1 kB, Player: 2")
            for a in sorted(ages, reverse=True)]


def test_a_server_printing_its_status_line_is_fine():
    assert instance_service.unresponsive_seconds(_Container(), _lines(30, 20, 10), NOW) is None


def test_no_status_line_for_five_minutes_means_it_stopped_responding():
    assert instance_service.unresponsive_seconds(_Container(), _lines(299), NOW) is None
    assert instance_service.unresponsive_seconds(_Container(), _lines(900, 400), NOW) == 400


def test_other_log_lines_do_not_count_as_a_sign_of_life():
    lines = _lines(400) + [(NOW - timedelta(seconds=5), "SCRIPT : something")]
    assert instance_service.unresponsive_seconds(_Container(), lines, NOW) == 400


def test_a_tail_without_any_status_line_is_no_evidence_of_a_freeze():
    lines = [(NOW - timedelta(seconds=900), "SCRIPT : chatty mod")]
    assert instance_service.unresponsive_seconds(_Container(), lines, NOW) is None


def test_a_longer_logstats_interval_of_the_users_own_is_allowed_for():
    slow = _Container("-logStats 600000")  # every ten minutes
    assert instance_service.unresponsive_seconds(slow, _lines(900), NOW) is None
    assert instance_service.unresponsive_seconds(slow, _lines(1801), NOW) == 1801


def _frozen_harness(monkeypatch, inst, frozen_for):
    from services import docker_service

    container = _Container()
    container.status = "running"
    restarted = []
    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service, "_all_instances", lambda _s: [inst])
    monkeypatch.setattr(docker_service, "find_instance_container", lambda _i: container)
    monkeypatch.setattr(instance_service, "current_run_log_lines", lambda c, **k: [])
    monkeypatch.setattr(instance_service, "server_state", lambda c, t: "online")
    monkeypatch.setattr(instance_service, "unresponsive_seconds", lambda c, lines: frozen_for)
    monkeypatch.setattr(instance_service, "restart_instance", lambda i: restarted.append(i))
    return restarted


def test_a_frozen_server_is_restarted_where_that_is_switched_on(monkeypatch):
    restarted = _frozen_harness(monkeypatch, _inst(restart_unresponsive=True), 700)
    instance_service.restart_unresponsive_servers()
    assert restarted == [1]


def test_it_is_given_ten_minutes_first(monkeypatch):
    restarted = _frozen_harness(monkeypatch, _inst(restart_unresponsive=True), 400)
    instance_service.restart_unresponsive_servers()
    assert restarted == []


def test_a_frozen_server_is_left_alone_by_default(monkeypatch):
    restarted = _frozen_harness(monkeypatch, _inst(), 7000)
    instance_service.restart_unresponsive_servers()
    assert restarted == []


def test_battleye_off_is_read_from_the_config():
    on = TemplateSpec(name="t", scenario_id="x").to_config()
    off = TemplateSpec(name="t", scenario_id="x", battleye=False).to_config()
    assert instance_service._battleye_on(json.dumps(on)) is True
    assert instance_service._battleye_on(json.dumps(off)) is False
    assert instance_service._battleye_on(None) is True


# --------------------------------------------------------------------------- #
# The stop timeout
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    (None, 60), ("", 60), ("abc", 60), ("120", 120), ("3", 10), ("99999", 600),
])
def test_stop_timeout_setting(raw, expected):
    assert config._stop_timeout(raw) == expected


def test_stop_uses_the_configured_timeout_and_says_when_it_ended_in_a_kill(monkeypatch, caplog):
    from services import docker_service

    class Stopped:
        id = "c1"
        attrs = {"State": {"ExitCode": 137, "OOMKilled": False}}

        def stop(self, timeout):
            self.timeout = timeout

        def reload(self):
            pass

    container = Stopped()
    with Session(get_engine()) as session:
        session.add(_inst(id=None))
        session.commit()
        iid = session.exec(select(Instance)).first().id
    monkeypatch.setattr(config.settings, "instance_stop_timeout", 75)
    monkeypatch.setattr(docker_service, "find_instance_container", lambda _i: container)

    with caplog.at_level("WARNING"):
        instance_service.stop_instance(iid)

    assert container.timeout == 75
    assert "was killed" in caplog.text


# --------------------------------------------------------------------------- #
# Limits on the container
# --------------------------------------------------------------------------- #

def test_resource_kwargs():
    assert instance_service._resource_kwargs(_inst()) == {}
    kwargs = instance_service._resource_kwargs(_inst(mem_limit_mb=8192, cpuset="0-3"))
    assert kwargs == {"mem_limit": 8192 * 2**20, "memswap_limit": 8192 * 2**20,
                      "cpuset_cpus": "0-3"}


def test_a_changed_limit_rebuilds_the_container():
    class C:
        attrs = {"HostConfig": {"Memory": 0, "CpusetCpus": ""}}

        def reload(self):
            pass

    assert instance_service._container_resources_match(C(), _inst()) is True
    assert instance_service._container_resources_match(C(), _inst(mem_limit_mb=4096)) is False
    assert instance_service._container_resources_match(C(), _inst(cpuset="0,1")) is False


def test_limits_are_validated_and_stored():
    with Session(get_engine()) as session:
        session.add(_inst(id=None))
        session.commit()
        iid = session.exec(select(Instance)).first().id

    with pytest.raises(InstanceError):
        instance_service.set_resources(iid, 512, "")      # killed while loading
    with pytest.raises(InstanceError):
        instance_service.set_resources(iid, None, "0-")   # not a CPU list
    instance_service.set_resources(iid, 8192, "0-3, 6")

    with Session(get_engine()) as session:
        inst = session.get(Instance, iid)
        assert (inst.mem_limit_mb, inst.cpuset) == (8192, "0-3,6")
    instance_service.set_resources(iid, None, "")
    with Session(get_engine()) as session:
        inst = session.get(Instance, iid)
        assert (inst.mem_limit_mb, inst.cpuset) == (None, "")


SERVER = "ghcr.io/acemod/arma-reforger:latest"
SCOPE = policy.Scope(
    stack="team2", images=frozenset({SERVER}), host_network_images=frozenset({SERVER}),
    networks=frozenset({"team2-net"}), volumes=frozenset({"team2-data"}), api=(1, 47),
)


def _body(**host):
    return {"Image": SERVER, "Labels": {"reforger-manager.role": "instance"},
            "HostConfig": {"NetworkMode": "host", **host}}


def test_gate_lets_a_size_limited_log_and_resource_limits_through():
    policy.check_create(_body(
        LogConfig={"Type": "json-file", "Config": {"max-size": "50m", "max-file": "3"}},
        Memory=8 * 2**30, MemorySwap=8 * 2**30, CpusetCpus="0-3",
    ), "team2-instance-1", SCOPE)


@pytest.mark.parametrize("log", [
    {"Type": "syslog", "Config": {"syslog-address": "udp://203.0.113.9:514"}},
    {"Type": "json-file", "Config": {"labels": "x"}},
    {"Type": "json-file", "config": {}},
    "json-file",
])
def test_gate_refuses_a_log_that_could_leave_the_host(log):
    with pytest.raises(Denied):
        policy.check_create(_body(LogConfig=log), "team2-instance-1", SCOPE)


# --------------------------------------------------------------------------- #
# Restart options
# --------------------------------------------------------------------------- #

def _schedule(**over):
    return json.dumps({"times": ["04:00"], **over})


def test_schedule_options_default_to_every_day_no_wait_no_backup():
    opts = instance_service.schedule_options(_inst(restart_schedule_json=_schedule()))
    assert opts == {"times": ["04:00"], "days": [], "wait_empty_minutes": 0, "backup": False}
    assert instance_service.schedule_options(_inst())["times"] == []


def test_all_seven_days_is_every_day():
    assert instance_service._normalise_days([6, 0, 1, 2, 3, 4, 5]) == []
    assert instance_service._normalise_days([5, 0, 5]) == [0, 5]
    with pytest.raises(InstanceError):
        instance_service._normalise_days([7])
    with pytest.raises(InstanceError):
        instance_service._normalise_wait(46)


def test_a_restart_is_due_only_on_its_days():
    wed = datetime(2026, 11, 11, 4, 0, 30)  # a Wednesday
    assert instance_service._due_scheduled_restart(["04:00"], wed, None, 3600, [2]) is not None
    assert instance_service._due_scheduled_restart(["04:00"], wed, None, 3600, [0, 4]) is None
    assert instance_service._due_scheduled_restart(["04:00"], wed, None, 3600, []) is not None


def test_the_next_restart_skips_to_the_next_day_it_runs_on():
    wed = datetime(2026, 11, 11, 12, 0)
    assert instance_service._next_scheduled_restart(["04:00"], wed) == datetime(2026, 11, 12, 4, 0)
    assert instance_service._next_scheduled_restart(["04:00"], wed, [0]) == \
        datetime(2026, 11, 16, 4, 0)
    assert instance_service._next_scheduled_restart(["04:00", "16:00"], wed, [2]) == \
        datetime(2026, 11, 11, 16, 0)


def test_saving_the_times_alone_keeps_the_other_options():
    with Session(get_engine()) as session:
        session.add(_inst(id=None))
        session.commit()
        iid = session.exec(select(Instance)).first().id

    instance_service.set_restart_schedule(iid, ["04:00"], [0, 4], 30, True)
    instance_service.set_restart_schedule(iid, ["05:00"])

    with Session(get_engine()) as session:
        opts = instance_service.schedule_options(session.get(Instance, iid))
    assert opts == {"times": ["05:00"], "days": [0, 4], "wait_empty_minutes": 30, "backup": True}


def _due_harness(monkeypatch, insts, players=0):
    from services import docker_service

    calls = []
    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service, "_all_instances", lambda _s: insts)
    monkeypatch.setattr(instance_service, "_players_online", lambda _i: players)
    monkeypatch.setattr(instance_service, "restart_instance",
                        lambda i, update_mods=False: calls.append(("restart", i)))
    monkeypatch.setattr(instance_service, "stop_instance", lambda i: calls.append(("stop", i)))
    monkeypatch.setattr(instance_service, "start_instance",
                        lambda i, update_mods=False: calls.append(("start", i)))
    monkeypatch.setattr(instance_service, "_backup_at_rest",
                        lambda inst: calls.append(("backup", inst.id)))
    monkeypatch.setattr(instance_service, "_waiting_for_empty", set())
    return calls


def _due_now(minutes_ago=1, **over):
    """A schedule whose time came `minutes_ago`, and a last-serviced before it."""
    at = datetime.now() - timedelta(minutes=minutes_ago)
    return dict(
        restart_schedule_json=json.dumps({"times": [at.strftime("%H:%M")], **over}),
        last_scheduled_restart=at - timedelta(hours=2),
    )


def _midnight_safe():
    # The harness builds "a few minutes ago" on today's date.
    now = datetime.now()
    return now.hour > 0 or now.minute > 50


def test_a_due_restart_waits_while_players_are_on(monkeypatch):
    if not _midnight_safe():
        pytest.skip("too close to midnight for a same-day schedule")
    inst = _inst(**_due_now(5, wait_empty_minutes=30))
    calls = _due_harness(monkeypatch, [inst], players=4)

    instance_service.apply_scheduled_restarts()

    assert calls == []


def test_it_restarts_as_soon_as_the_server_is_empty(monkeypatch):
    if not _midnight_safe():
        pytest.skip("too close to midnight for a same-day schedule")
    inst = _inst(**_due_now(5, wait_empty_minutes=30))
    with Session(get_engine()) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
        session.expunge(inst)
    calls = _due_harness(monkeypatch, [inst], players=0)

    instance_service.apply_scheduled_restarts()

    assert calls == [("restart", inst.id)]


def test_it_restarts_anyway_once_the_wait_is_over(monkeypatch):
    if not _midnight_safe():
        pytest.skip("too close to midnight for a same-day schedule")
    inst = _inst(**_due_now(40, wait_empty_minutes=30))
    with Session(get_engine()) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
        session.expunge(inst)
    calls = _due_harness(monkeypatch, [inst], players=4)

    instance_service.apply_scheduled_restarts()

    assert calls == [("restart", inst.id)]


def test_the_backup_is_taken_between_the_stop_and_the_start(monkeypatch):
    if not _midnight_safe():
        pytest.skip("too close to midnight for a same-day schedule")
    inst = _inst(**_due_now(1, backup=True))
    with Session(get_engine()) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
        session.expunge(inst)
    calls = _due_harness(monkeypatch, [inst])

    instance_service.apply_scheduled_restarts()

    assert calls == [("stop", inst.id), ("backup", inst.id), ("start", inst.id)]


def test_a_failed_backup_does_not_leave_the_server_down(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(instance_backup, "create_backup", boom)
    instance_service._backup_at_rest(_inst())  # logs, does not raise


def test_scheduled_backups_have_a_shelf_of_their_own(tmp_path, monkeypatch):
    listed = (
        [{"id": f"m{n}", "source": "manual"} for n in range(3)]
        + [{"id": f"s{n}", "source": instance_backup.SOURCE_SCHEDULED} for n in range(4)]
    )
    deleted = []
    monkeypatch.setattr(instance_backup, "KEEP", 2)
    monkeypatch.setattr(instance_backup, "KEEP_SCHEDULED", 3)
    monkeypatch.setattr(instance_backup, "list_backups", lambda _i: listed)
    monkeypatch.setattr(instance_backup, "delete_backup", lambda _i, b: deleted.append(b))

    instance_backup.prune(1)

    assert deleted == ["m2", "s3"]


# --------------------------------------------------------------------------- #
# The Workshop's health
# --------------------------------------------------------------------------- #

class _Client:
    def __init__(self, responder):
        self._responder = responder

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None):
        return self._responder(url)


def _response(status, text=""):
    return httpx.Response(status, text=text, request=httpx.Request("GET", "https://x/"))


GOOD = ('<script id="__NEXT_DATA__" type="application/json">'
        '{"buildId": "b", "props": {"pageProps": {"ok": 1}}}</script>')


def _service(monkeypatch, responder):
    service = WorkshopService()
    monkeypatch.setattr(service, "_client", lambda: _Client(responder))
    return service


def test_workshop_is_unreadable_after_several_failures_in_a_row(monkeypatch):
    service = _service(monkeypatch, lambda url: _response(200, "<html>redesigned</html>"))
    for _ in range(UNHEALTHY_AFTER - 1):
        with pytest.raises(ValueError):
            service._get_page_props("/workshop")
    assert service.health()["ok"] is True
    with pytest.raises(ValueError):
        service._get_page_props("/workshop")
    health = service.health()
    assert health["ok"] is False and health["failures"] == UNHEALTHY_AFTER
    assert health["failing_since"] is not None


def test_one_good_answer_makes_it_healthy_again(monkeypatch):
    answers = [_response(503)] * UNHEALTHY_AFTER + [_response(200, GOOD)]
    service = _service(monkeypatch, lambda url: answers.pop(0))
    for _ in range(UNHEALTHY_AFTER):
        with pytest.raises(httpx.HTTPStatusError):
            service._get_page_props("/workshop")
    assert service.health()["ok"] is False

    assert service._get_page_props("/workshop") == {"ok": 1}
    assert service.health() == {"ok": True, "failures": 0, "failing_since": None, "error": ""}


def test_a_mod_that_is_gone_says_nothing_about_the_workshop(monkeypatch):
    service = _service(monkeypatch, lambda url: _response(404))
    for _ in range(10):
        with pytest.raises(httpx.HTTPStatusError):
            service._get_page_props("/workshop/0000000000000000")
    assert service.health()["ok"] is True


def test_new_templates_require_their_mods_by_default():
    assert TemplateSpec(name="t", scenario_id="x").to_config()["game"]["modsRequiredByDefault"] \
        is True
