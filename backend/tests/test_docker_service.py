import pytest
from docker.errors import DockerException

from services import docker_service


@pytest.fixture(autouse=True)
def _clear_info_cache():
    docker_service._info = None
    yield
    docker_service._info = None


class _FakeClient:
    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    def info(self):
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_daemon_info_does_not_cache_a_failure(monkeypatch):
    # Caching the failure pinned the answer for the life of the process: one unlucky
    # call at startup and is_docker_desktop() stayed False forever, so a Windows host
    # was shown the LINUX firewall command for good (#85).
    client = _FakeClient(
        DockerException("daemon not up yet"),   # 1st call: still booting
        DockerException("daemon not up yet"),   # 2nd call: still booting
        {"OperatingSystem": "Docker Desktop"},  # 3rd call: it's up
    )
    monkeypatch.setattr(docker_service, "get_client", lambda: client)

    assert docker_service.daemon_info() == {}          # daemon down
    assert docker_service.is_docker_desktop() is False  # asks again, still down

    # ...daemon comes up: the next call asks again rather than serving the stale {}.
    assert docker_service.daemon_info() == {"OperatingSystem": "Docker Desktop"}
    assert docker_service.is_docker_desktop() is True
    assert client.calls == 3  # two retries, then cached


def test_daemon_info_caches_success(monkeypatch):
    client = _FakeClient({"OperatingSystem": "Ubuntu 24.04"})
    monkeypatch.setattr(docker_service, "get_client", lambda: client)

    docker_service.daemon_info()
    docker_service.daemon_info()
    assert client.calls == 1  # success is cached; we don't hammer the daemon
