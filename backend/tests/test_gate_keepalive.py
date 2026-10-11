"""The gate and the observer outlast every poll on an idle connection.

With uvicorn's default 5 s keep-alive and the GUI's 5 s poll, the server closed
pooled connections just as the manager reused them, and a page got a 500
("Remote end closed connection without response").
"""
import pytest

from gate import KEEP_ALIVE_SECONDS, observer, policy
from gate import __main__ as gate_main

# The slowest poll that reaches Docker: the update check, every 60 s.
LONGEST_POLL_SECONDS = 60


@pytest.fixture
def served(monkeypatch):
    """The keyword arguments the module passed to uvicorn.run."""
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))
    return seen


def test_keep_alive_is_well_past_every_poll():
    assert KEEP_ALIVE_SECONDS >= 5 * LONGEST_POLL_SECONDS


def test_the_gate_serves_with_the_long_keep_alive(served, monkeypatch, tmp_path):
    monkeypatch.setattr(gate_main, "GATE_SOCKET", str(tmp_path / "docker.sock"))
    monkeypatch.setattr(gate_main, "build_scope", lambda: policy.Scope(
        stack="team2", images=frozenset(), host_network_images=frozenset(),
        networks=frozenset({"team2-net"}), volumes=frozenset(), api=(1, 47),
    ))
    gate_main.main()
    assert served["timeout_keep_alive"] == KEEP_ALIVE_SECONDS


def test_the_observer_serves_with_the_long_keep_alive(served, monkeypatch, tmp_path):
    monkeypatch.setattr(observer, "OBSERVER_SOCKET", str(tmp_path / "observer.sock"))
    monkeypatch.setattr(observer, "_client", lambda: object())
    observer.main()
    assert served["timeout_keep_alive"] == KEEP_ALIVE_SECONDS
