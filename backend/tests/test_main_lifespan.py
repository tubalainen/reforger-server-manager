"""The background monitor must survive a Docker daemon that is late to the party (#85)."""
import asyncio

import pytest

import main
from services import docker_service


@pytest.mark.anyio
async def test_monitor_starts_even_when_docker_is_down_at_boot(monkeypatch):
    # It used to be created ONLY if this first ping succeeded, so a daemon that was
    # merely slow (host reboot, cold Docker Desktop) silently cost crash recovery,
    # scheduled restarts and log pruning for the whole life of the process.
    monkeypatch.setattr(docker_service, "ping", lambda: False)
    started = []
    monkeypatch.setattr(main.asyncio, "create_task", lambda coro: started.append(coro) or _Dummy(coro))

    async with main.lifespan(None):
        pass

    assert started, "the monitor task must be created even with the daemon down"
    started[0].close()


@pytest.mark.anyio
async def test_shutdown_stops_the_game_servers(monkeypatch):
    # docker compose down must not leave sibling server containers holding the
    # compose network hostage ('resource is still in use', #113).
    monkeypatch.setattr(docker_service, "ping", lambda: True)
    monkeypatch.setattr(main.asyncio, "create_task", lambda coro: _Dummy(coro))
    calls = []
    monkeypatch.setattr(
        main.instance_service, "shutdown_all_instances", lambda: calls.append("shutdown")
    )

    async with main.lifespan(None):
        assert calls == []  # only on the way OUT

    assert calls == ["shutdown"]


@pytest.mark.anyio
async def test_monitor_pass_no_ops_while_docker_is_down_then_recovers(monkeypatch):
    up = {"docker": False}
    calls = {"reconcile": 0, "cleanup": 0}

    monkeypatch.setattr(docker_service, "ping", lambda: up["docker"])
    monkeypatch.setattr(
        docker_service, "remove_exited",
        lambda role: calls.__setitem__("cleanup", calls["cleanup"] + 1),
    )
    monkeypatch.setattr(
        main.instance_service, "reconcile_and_recover",
        lambda: calls.__setitem__("reconcile", calls["reconcile"] + 1),
    )
    monkeypatch.setattr(main.instance_service, "apply_scheduled_restarts", lambda: None)
    monkeypatch.setattr(main.instance_service, "prune_old_logs", lambda: 0)

    # Run the monitor for a few ticks with a no-op sleep, flipping Docker on midway.
    ticks = {"n": 0}

    async def fake_sleep(_seconds):
        ticks["n"] += 1
        if ticks["n"] == 2:
            up["docker"] = True
        if ticks["n"] >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main._crash_monitor()

    # It waited the daemon out instead of giving up on it...
    assert calls["reconcile"] > 0
    # ...and the one-time steamcmd cleanup ran once Docker was actually there.
    assert calls["cleanup"] == 1


class _Dummy:
    def __init__(self, coro):
        self._coro = coro

    def cancel(self):
        self._coro.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
