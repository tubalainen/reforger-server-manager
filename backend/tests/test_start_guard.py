"""A server that never comes online is not brought back for ever."""
import pytest

from services import instance_service, start_guard


@pytest.fixture(autouse=True)
def _clean():
    start_guard._runs.clear()
    start_guard._given_up.clear()
    yield
    start_guard._runs.clear()
    start_guard._given_up.clear()


def _never():
    return False


# --------------------------------------------------------------------------- #
# The count
# --------------------------------------------------------------------------- #

def test_a_run_that_ends_without_coming_online_is_a_failed_start():
    assert start_guard.observe(1, "t1", 0, True, _never) == 0   # still loading
    assert start_guard.observe(1, "t1", 0, False, _never) == 1  # went down
    assert start_guard.observe(1, "t1", 0, False, _never) == 1  # counted once


def test_docker_restarting_it_counts_each_run_that_never_came_online():
    start_guard.observe(1, "t1", 0, True, _never)
    assert start_guard.observe(1, "t2", 1, True, _never) == 1
    assert start_guard.observe(1, "t3", 2, True, _never) == 2
    assert start_guard.observe(1, "t4", 3, True, _never) == 3


def test_restarts_between_two_looks_are_all_counted():
    # Docker's first restarts are fractions of a second apart; the monitor
    # looks every 15 seconds.
    start_guard.observe(1, "t1", 0, True, _never)
    assert start_guard.observe(1, "t4", 3, True, _never) == 3


def test_coming_online_resets_the_count():
    start_guard.observe(1, "t1", 0, True, _never)
    assert start_guard.observe(1, "t2", 1, True, _never) == 1
    assert start_guard.observe(1, "t2", 1, True, lambda: True) == 0
    # A crash after it was online is a crash, not a failed start.
    assert start_guard.observe(1, "t2", 1, False, _never) == 0
    assert start_guard.observe(1, "t3", 2, True, _never) == 0


def test_a_server_already_down_at_first_sight_is_not_a_failed_start():
    # The manager just started: this process never watched that run.
    assert start_guard.observe(1, "t1", 0, False, _never) == 0


def test_online_is_asked_only_until_the_run_has_been_seen_online():
    asked = []

    def online():
        asked.append(1)
        return True

    start_guard.observe(1, "t1", 0, True, online)
    start_guard.observe(1, "t1", 0, True, online)
    assert asked == [1]


def test_a_long_start_is_never_a_failure_while_it_is_still_running():
    for _ in range(500):  # two hours of mod download at one look every 15 s
        assert start_guard.observe(1, "t1", 0, True, _never) == 0


# --------------------------------------------------------------------------- #
# The monitor
# --------------------------------------------------------------------------- #

class _Container:
    def __init__(self, started="t1", restarts=0):
        self.id = "c1"
        self.attrs = {"State": {"StartedAt": started}, "RestartCount": restarts}


def _harness(monkeypatch, inst, container, status):
    from services import docker_service

    state = {"status": status}
    started, stopped = [], []
    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(instance_service, "_all_instances", lambda _s: [inst])
    monkeypatch.setattr(instance_service, "server_files_ready", lambda _b: True)
    monkeypatch.setattr(instance_service, "_monitor_containers", lambda: {inst.id: container})
    monkeypatch.setattr(instance_service, "container_status", lambda iid, *a, **k: state["status"])
    monkeypatch.setattr(instance_service, "_run_is_online", lambda c: False)
    monkeypatch.setattr(instance_service, "_seen_running", set())

    def fake_start(iid):
        start_guard.clear(iid)  # as the real one does
        started.append(iid)
        container.attrs["State"]["StartedAt"] = f"run{len(started) + 1}"
        state["status"] = "running"

    def fake_stop(iid):
        start_guard.forget_run(iid)
        stopped.append(iid)

    monkeypatch.setattr(instance_service, "start_instance", fake_start)
    monkeypatch.setattr(instance_service, "stop_instance", fake_stop)
    return state, started, stopped


def _inst(**over):
    from models import Instance

    base = dict(id=1, name="Alpha", template_id=1, branch="stable", game_port=2001,
                a2s_port=17777, rcon_port=19999, desired_state="running",
                auto_start=True, auto_restart=True)
    base.update(over)
    return Instance(**base)


def test_the_monitor_stops_a_server_that_keeps_failing_to_start(monkeypatch):
    inst = _inst()
    container = _Container()
    state, started, stopped = _harness(monkeypatch, inst, container, "running")

    instance_service.reconcile_and_recover()  # sees run 1 loading
    for _ in range(2):
        state["status"] = "exited"            # the run ends without coming online
        instance_service.reconcile_and_recover()
    assert started == [1, 1] and stopped == []  # brought back twice

    state["status"] = "exited"                # the third failed start
    instance_service.reconcile_and_recover()

    assert stopped == [1]
    assert started == [1, 1]                  # and not a third time
    assert start_guard.given_up(1)["attempts"] == 3


def test_docker_restart_loop_is_stopped_too(monkeypatch):
    inst = _inst()
    container = _Container()
    _state, started, stopped = _harness(monkeypatch, inst, container, "running")

    instance_service.reconcile_and_recover()
    container.attrs["State"]["StartedAt"] = "t2"   # Docker's policy restarted it,
    container.attrs["RestartCount"] = 3            # three times since the last look
    instance_service.reconcile_and_recover()

    assert stopped == [1] and started == []


def test_a_server_that_crashes_after_coming_online_is_always_recovered(monkeypatch):
    inst = _inst()
    container = _Container()
    state, started, stopped = _harness(monkeypatch, inst, container, "running")
    monkeypatch.setattr(instance_service, "_run_is_online", lambda c: True)

    for _ in range(6):
        instance_service.reconcile_and_recover()   # online
        state["status"] = "exited"                 # crashes
        instance_service.reconcile_and_recover()   # recovered

    assert len(started) == 6 and stopped == []


def test_starting_it_by_hand_begins_the_count_again():
    start_guard.observe(1, "t1", 0, True, _never)
    start_guard.observe(1, "t2", 2, True, _never)
    start_guard.give_up(1, 3)

    start_guard.clear(1)

    assert start_guard.given_up(1) is None
    assert start_guard.observe(1, "t3", 0, True, _never) == 0
