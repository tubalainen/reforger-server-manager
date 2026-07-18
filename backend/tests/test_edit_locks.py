"""Template edit-lock service (#102): per-tab locks with heartbeat expiry."""
import pytest

from services import edit_locks


class FakeClock:
    """Stands in for the time module so expiry is tested without sleeping."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(edit_locks, "time", c)
    edit_locks._locks.clear()
    yield c
    edit_locks._locks.clear()


def test_acquire_release_roundtrip(clock):
    assert edit_locks.holder(1) is None
    assert edit_locks.acquire(1, "tab-a") is True
    assert edit_locks.holder(1) == "tab-a"
    # the holder renews; anyone else is refused
    assert edit_locks.acquire(1, "tab-a") is True
    assert edit_locks.acquire(1, "tab-b") is False
    # a non-holder's release is a no-op; the holder's frees the template
    edit_locks.release(1, "tab-b")
    assert edit_locks.holder(1) == "tab-a"
    edit_locks.release(1, "tab-a")
    assert edit_locks.holder(1) is None
    assert edit_locks.acquire(1, "tab-b") is True


def test_held_by_other(clock):
    edit_locks.acquire(1, "tab-a")
    assert edit_locks.held_by_other(1, "tab-a") is False
    assert edit_locks.held_by_other(1, "tab-b") is True
    assert edit_locks.held_by_other(2, "tab-b") is False


def test_lock_expires_without_heartbeat(clock):
    edit_locks.acquire(1, "tab-a")
    clock.now += edit_locks.LOCK_TTL_SECONDS + 1
    assert edit_locks.holder(1) is None
    assert edit_locks.acquire(1, "tab-b") is True


def test_heartbeat_keeps_lock_alive(clock):
    edit_locks.acquire(1, "tab-a")
    # total elapsed exceeds the TTL, but each beat lands inside it
    for _ in range(4):
        clock.now += edit_locks.LOCK_TTL_SECONDS - 10
        assert edit_locks.acquire(1, "tab-a") is True
    assert edit_locks.acquire(1, "tab-b") is False


def test_clear_all(clock):
    edit_locks.acquire(1, "tab-a")
    edit_locks.acquire(2, "tab-b")
    assert edit_locks.clear_all() == 2
    assert edit_locks.holder(1) is None
    assert edit_locks.acquire(1, "tab-c") is True


def test_valid_client_id():
    assert edit_locks.valid_client_id("tab-a") is True
    assert edit_locks.valid_client_id("") is False
    assert edit_locks.valid_client_id("x" * 65) is False
