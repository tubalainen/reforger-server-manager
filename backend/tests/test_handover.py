"""rsm update leaves the game servers running while it replaces the manager."""
import time

import pytest

import config
import manage
from services import handover


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))


def test_no_request_means_the_servers_are_stopped_as_before():
    assert handover.take() is False


def test_a_fresh_request_is_honoured_once():
    handover.request()
    assert handover.take() is True
    assert handover.take() is False  # used up


def test_an_old_request_is_ignored():
    handover.request()
    assert handover.take(now=time.time() + handover.MAX_AGE_SECONDS + 5) is False


def test_a_manager_start_forgets_any_request():
    handover.request()
    handover.clear()
    assert handover.take() is False


def test_manage_keep_servers_makes_the_request(monkeypatch, capsys):
    monkeypatch.setattr(manage.os, "geteuid", lambda: 1000, raising=False)
    assert manage.main(["keep-servers"]) == 0
    assert "keep running" in capsys.readouterr().out
    assert handover.take() is True


@pytest.mark.anyio
async def test_a_manager_replaced_by_rsm_update_leaves_the_servers_running(monkeypatch):
    import main
    from services import docker_service

    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(main.asyncio, "create_task", lambda coro: _Dummy(coro))
    calls = []
    monkeypatch.setattr(main.instance_service, "shutdown_all_instances",
                        lambda: calls.append("shutdown"))

    async with main.lifespan(None):
        handover.request()  # rsm update, while this manager runs
    assert calls == []

    async with main.lifespan(None):
        pass  # an ordinary stop: the request was used up
    assert calls == ["shutdown"]


@pytest.mark.anyio
async def test_a_request_left_from_before_this_start_is_not_honoured(monkeypatch):
    import main
    from services import docker_service

    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(main.asyncio, "create_task", lambda coro: _Dummy(coro))
    calls = []
    monkeypatch.setattr(main.instance_service, "shutdown_all_instances",
                        lambda: calls.append("shutdown"))
    handover.request()  # made for a manager that never restarted
    async with main.lifespan(None):
        pass
    assert calls == ["shutdown"]


class _Dummy:
    def __init__(self, coro):
        self._coro = coro

    def cancel(self):
        self._coro.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
