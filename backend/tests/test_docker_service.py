from types import SimpleNamespace

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


# --------------------------------------------------------------------------- #
# docker_api_exposure (v0.64.1)
# --------------------------------------------------------------------------- #

_ISOLATED = {
    docker_service.GW_MODE_IPV4: "isolated",
    docker_service.GW_MODE_IPV6: "isolated",
}


class _Attrs:
    def __init__(self, attrs):
        self.attrs = attrs


class _ExposureClient:
    """The three reads the check makes: its own container, one network, the version."""

    def __init__(self, network, engine="29.7.2", fail_network=False):
        self.network = network
        self.engine = engine
        self.fail_network = fail_network
        self.network_lookups = []
        endpoints = {
            # The proxy (172.20.0.2) shares this one with the manager...
            "reforger-docker-api": {"IPAddress": "172.20.0.3", "IPPrefixLen": 16,
                                    "NetworkID": "api-id"},
            # ...and not this one.
            "reforger-net": {"IPAddress": "172.18.0.2", "IPPrefixLen": 16,
                             "NetworkID": "net-id"},
        }
        me = _Attrs({"NetworkSettings": {"Networks": endpoints}})
        self.containers = SimpleNamespace(get=lambda _hostname: me)
        self.networks = SimpleNamespace(get=self._network)

    def _network(self, net_id):
        self.network_lookups.append(net_id)
        if self.fail_network:
            raise DockerException("proxy said no")
        return _Attrs(self.network)

    def version(self):
        return {"Version": self.engine}


@pytest.fixture()
def exposure(monkeypatch):
    """A Linux host with host-networked game servers behind a TCP socket proxy."""
    docker_service._exposure = None
    docker_service._exposure_known = False
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
    monkeypatch.setattr(docker_service, "daemon_info", lambda: {"OperatingSystem": "Ubuntu"})
    monkeypatch.setattr(docker_service, "use_host_network", lambda: True)
    monkeypatch.setattr(docker_service.socket, "gethostbyname", lambda _h: "172.20.0.2")

    def use(client):
        monkeypatch.setattr(docker_service, "get_client", lambda: client)
        return client

    yield use
    docker_service._exposure = None
    docker_service._exposure_known = False


def test_isolated_network_on_new_engine_is_safe_and_cached(exposure):
    client = exposure(_ExposureClient({"Internal": True, "Options": _ISOLATED}))
    assert docker_service.docker_api_exposure() is None
    assert docker_service.docker_api_exposure() is None
    assert client.network_lookups == ["api-id"]  # the proxy's network, asked once
    assert docker_service.exposure_known() is True


def test_internal_network_without_gateway_mode_is_exposed(exposure):
    # Exactly what every compose file before v0.64.1 created.
    exposure(_ExposureClient({"Internal": True, "Options": {}}))
    warning = docker_service.docker_api_exposure()
    assert warning["id"] == "docker_api_exposed"
    assert warning["severity"] == "danger"
    assert "older than v0.64.1" in warning["detail"]
    assert "reforger-docker-api" in warning["detail"]
    assert "sudo rsm update" in warning["action"]


def test_old_engine_is_exposed_even_with_the_option_set(exposure):
    # Engines before 28 cannot isolate: 27 rejects the value, older ones ignore it.
    exposure(_ExposureClient({"Internal": True, "Options": _ISOLATED}, engine="27.5.1"))
    warning = docker_service.docker_api_exposure()
    assert "Docker Engine 27.5.1" in warning["detail"]
    # The v0.65.0 compose file needs no network for Docker, so no engine update.
    assert "v0.65.0" in warning["action"]
    assert "update Docker" not in warning["action"]


def test_non_internal_network_is_exposed(exposure):
    exposure(_ExposureClient({"Internal": False, "Options": {}}))
    assert "not an internal network" in docker_service.docker_api_exposure()["detail"]


def test_ipv6_network_needs_ipv6_isolated_too(exposure):
    only_v4 = {docker_service.GW_MODE_IPV4: "isolated"}
    exposure(_ExposureClient({"Internal": True, "EnableIPv6": True, "Options": only_v4}))
    assert docker_service.docker_api_exposure() is not None


def test_ipv6_option_is_not_required_without_ipv6(exposure):
    only_v4 = {docker_service.GW_MODE_IPV4: "isolated"}
    exposure(_ExposureClient({"Internal": True, "EnableIPv6": False, "Options": only_v4}))
    assert docker_service.docker_api_exposure() is None


def test_bridge_networked_servers_are_not_in_the_host_namespace(exposure, monkeypatch):
    # Docker Desktop (or INSTANCE_NETWORK_MODE=bridge): the servers are not the host.
    client = exposure(_ExposureClient({"Internal": True, "Options": {}}))
    monkeypatch.setattr(docker_service, "use_host_network", lambda: False)
    assert docker_service.docker_api_exposure() is None
    assert docker_service.exposure_known() is True
    assert client.network_lookups == []


@pytest.mark.parametrize("docker_host", ["", "unix:///var/run/docker.sock"])
def test_no_network_proxy_means_nothing_to_reach(exposure, monkeypatch, docker_host):
    client = exposure(_ExposureClient({"Internal": True, "Options": {}}))
    monkeypatch.setenv("DOCKER_HOST", docker_host)
    assert docker_service.docker_api_exposure() is None
    assert docker_service.exposure_known() is True
    assert client.network_lookups == []


def test_unmappable_proxy_gives_no_warning_and_asks_again(exposure, monkeypatch):
    # A custom setup whose proxy is on none of the manager's networks: we cannot
    # tell, so no false alarm — and no cached answer either.
    exposure(_ExposureClient({"Internal": True, "Options": {}}))
    monkeypatch.setattr(docker_service.socket, "gethostbyname", lambda _h: "10.9.9.9")
    assert docker_service.docker_api_exposure() is None
    assert docker_service.exposure_known() is False


def test_daemon_down_is_not_a_cached_answer(exposure, monkeypatch):
    exposure(_ExposureClient({"Internal": True, "Options": {}}))
    monkeypatch.setattr(docker_service, "daemon_info", lambda: {})
    assert docker_service.docker_api_exposure() is None
    assert docker_service.exposure_known() is False
    # ...and once Docker answers, the real state is found.
    monkeypatch.setattr(docker_service, "daemon_info", lambda: {"OperatingSystem": "Ubuntu"})
    assert docker_service.docker_api_exposure() is not None


def test_a_failed_read_is_not_a_cached_answer(exposure):
    exposure(_ExposureClient({"Internal": True, "Options": {}}, fail_network=True))
    assert docker_service.docker_api_exposure() is None
    assert docker_service.exposure_known() is False


# --------------------------------------------------------------------------- #
# Stack scoping (#204): a manager sees and touches only its own stack
# --------------------------------------------------------------------------- #
class _Container(SimpleNamespace):
    pass


def _c(name, **labels):
    return _Container(name=name, labels=labels)


class _ListClient:
    """containers.list() that honours label filters the way Docker does."""

    def __init__(self, containers):
        self._all = containers
        self.filters = []
        self.containers = SimpleNamespace(list=self._list)

    def _list(self, filters=None, **_kwargs):
        self.filters.append(filters)
        wanted = [f.split("=", 1) for f in (filters or {}).get("label", [])]
        return [c for c in self._all if all(c.labels.get(k) == v for k, v in wanted)]


_M = docker_service.LABEL_MANAGED
_S = docker_service.LABEL_STACK
_R = docker_service.LABEL_ROLE
_I = docker_service.LABEL_INSTANCE_ID


@pytest.fixture()
def on_stack(monkeypatch):
    def use(stack, containers):
        monkeypatch.setattr(docker_service.config.settings, "rsm_stack", stack)
        client = _ListClient(containers)
        monkeypatch.setattr(docker_service, "get_client", lambda: client)
        return client
    return use


def _instances(**by_name):
    return [_c(n, **{_M: "true", _R: "instance", _I: i, **extra})
            for n, (i, extra) in by_name.items()]


def test_the_default_stack_adopts_containers_from_before_stacks(on_stack):
    on_stack("reforger", _instances(
        legacy=("1", {}),                       # made before v0.65.0: no stack label
        mine=("2", {_S: "reforger"}),
        theirs=("3", {_S: "team2"}),
    ))
    found = docker_service.instance_containers()
    assert {i: c.name for i, c in found.items()} == {1: "legacy", 2: "mine"}


def test_another_stack_sees_only_its_own_and_asks_docker_for_them(on_stack):
    client = on_stack("team2", _instances(
        legacy=("1", {}),
        theirs=("2", {_S: "reforger"}),
        mine=("3", {_S: "team2"}),
    ))
    found = docker_service.instance_containers()
    assert {i: c.name for i, c in found.items()} == {3: "mine"}
    assert f"{_S}=team2" in client.filters[0]["label"]


def test_the_labelled_container_wins_over_an_adopted_one(on_stack):
    # A recreate that did not get to remove the old container: both carry id 4.
    on_stack("reforger", _instances(old=("4", {}), new=("4", {_S: "reforger"})))
    assert docker_service.instance_containers()[4].name == "new"
    assert docker_service.find_instance_container(4).name == "new"


def test_containers_the_manager_did_not_make_are_not_its_own(on_stack):
    on_stack("reforger", [_c("stranger", **{_R: "instance", _I: "5"})])
    assert docker_service.instance_containers() == {}


def test_names_and_labels_carry_the_stack(monkeypatch):
    monkeypatch.setattr(docker_service.config.settings, "rsm_stack", "team2")
    assert docker_service.container_name("instance-3") == "team2-instance-3"
    assert docker_service.managed_labels(**{_R: "steamcmd"}) == {
        _M: "true", _S: "team2", _R: "steamcmd",
    }
    assert docker_service.is_current_stack(_c("x", **{_S: "team2"}))
    assert not docker_service.is_current_stack(_c("x", **{_M: "true"}))


# --------------------------------------------------------------------------- #
# Is the manager behind its own gate? (#204)
# --------------------------------------------------------------------------- #
class _GateApi:
    base_url = "http+docker://localhost"

    def __init__(self, status, body=None, fail=False):
        self.status, self.body, self.fail = status, body, fail
        self.asked = 0

    def get(self, url, timeout=None):
        self.asked += 1
        if self.fail:
            raise DockerException("socket gone")
        return SimpleNamespace(status_code=self.status, json=lambda: self.body)


@pytest.fixture()
def gate(monkeypatch):
    docker_service._gate = None
    docker_service._gate_known = False
    monkeypatch.setattr(docker_service.config.settings, "rsm_stack", "reforger")
    monkeypatch.setattr(docker_service, "daemon_info", lambda: {"OperatingSystem": "Ubuntu"})

    def use(api):
        monkeypatch.setattr(docker_service, "get_client", lambda: SimpleNamespace(api=api))
        return api

    yield use
    docker_service._gate = None
    docker_service._gate_known = False


def test_behind_its_own_gate_there_is_nothing_to_say(gate):
    api = gate(_GateApi(200, {"gate": "rsm", "stack": "reforger", "version": "0.65.0"}))
    assert docker_service.gate_warning() is None
    assert docker_service.gate_warning() is None
    assert api.asked == 1  # a certain answer is cached


def test_an_old_compose_file_is_named(gate):
    # The socket proxy of an older compose file refuses the path outright.
    gate(_GateApi(403, {"message": "forbidden"}))
    warning = docker_service.gate_warning()
    assert warning["id"] == "docker_gate_missing"
    assert "older than v0.65.0" in warning["title"]


def test_a_gate_for_another_stack_is_a_danger(gate):
    gate(_GateApi(200, {"gate": "rsm", "stack": "team2"}))
    warning = docker_service.gate_warning()
    assert warning["id"] == "docker_gate_stack_mismatch"
    assert warning["severity"] == "danger"


def test_no_warning_on_a_blind_read(gate, monkeypatch):
    api = gate(_GateApi(200, {}, fail=True))
    assert docker_service.gate_warning() is None
    monkeypatch.setattr(docker_service, "daemon_info", lambda: {})
    api.fail = False
    assert docker_service.gate_warning() is None  # daemon down: not asked, not cached
    assert docker_service._gate_known is False
