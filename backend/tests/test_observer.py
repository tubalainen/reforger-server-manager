"""The Supervisor's observer (#204, v0.67.0): a read-only window on every stack.

What matters is what it does NOT pass on: environments (a manager's holds its
team's password), log text (it names the players), other people's containers,
and any request that would change something.
"""
import json

import pytest
from docker.errors import NotFound
from starlette.testclient import TestClient

import stacks
from gate import observer
from services import container_reading

STARTED = "2026-09-28T10:00:00.000000000Z"
SECRET = "hunter2-team-password"


def _summary(cid, name, labels=None):
    return {"Id": cid, "Names": [f"/{name}"], "Labels": labels or {}}


def _attrs(cid, name, labels=None, running=True, env=None):
    return {
        "Id": cid,
        "Name": f"/{name}",
        "Created": "2026-09-28T09:00:00Z",
        "Config": {
            "Image": "ghcr.io/acemod/arma-reforger:latest",
            "Labels": {**(labels or {}), "org.opencontainers.image.source": "x"},
            "Env": env or [f"ADMIN_PASSWORD={SECRET}"],
            "Cmd": ["--secret-flag"],
        },
        "State": {"Status": "running" if running else "exited", "Running": running,
                  "Restarting": False, "OOMKilled": False, "ExitCode": 0,
                  "StartedAt": STARTED, "FinishedAt": "0001-01-01T00:00:00Z"},
        "RestartCount": 0,
        "HostConfig": {"NetworkMode": "host", "RestartPolicy": {"Name": "unless-stopped"},
                       "Binds": ["/srv/secret:/x"],
                       "PortBindings": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "7780"}]}},
        "Mounts": [{"Source": "/srv/secret"}],
    }


MANAGER = {stacks.LABEL_STACK: "team2", stacks.LABEL_ROLE: "manager",
           stacks.LABEL_WEB_PORT: "7781"}
SERVER = {stacks.LABEL_MANAGED: "true", stacks.LABEL_STACK: "team2",
          stacks.LABEL_ROLE: "instance", stacks.LABEL_INSTANCE_ID: "1",
          stacks.LABEL_NAME: "Conflict", stacks.LABEL_MAX_PLAYERS: "64"}
LEGACY_SERVER = {stacks.LABEL_MANAGED: "true", stacks.LABEL_ROLE: "instance"}

ID_MANAGER = "a" * 64
ID_GATE = "b" * 64
ID_SERVER = "c" * 64
ID_OTHER = "d" * 64
ID_LOOKALIKE = "e" * 64

LOG = (
    f"{STARTED[:19]}.100000000Z  NETWORK : Player Alice connected from 198.51.100.7\n"
    f"{STARTED[:19]}.200000000Z  DEFAULT : Entered online game state.\n"
    f"{STARTED[:19]}.300000000Z  FPS: 59.9, frame time (avg: 16.7 ms), Mem: 1190747 kB, "
    "Player: 3, AI: 104\n"
)


class FakeContainer:
    def __init__(self, attrs):
        self.attrs = attrs
        self.id = attrs["Id"]

    def logs(self, **_kw):
        return LOG.encode()

    def stats(self, stream=False):
        return {"cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000},
                "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
                "memory_stats": {"usage": 1024, "limit": 4096}}


class FakeClient:
    def __init__(self):
        self.all = {
            ID_MANAGER: _attrs(ID_MANAGER, "team2-manager", MANAGER),
            # A gate from a compose file that did not label it yet.
            ID_GATE: _attrs(ID_GATE, "team2-docker-gate"),
            ID_SERVER: _attrs(ID_SERVER, "team2-instance-1", SERVER),
            ID_OTHER: _attrs(ID_OTHER, "postgres"),
            # Named like a manager, but of no stack anyone has.
            ID_LOOKALIKE: _attrs(ID_LOOKALIKE, "nextcloud-manager"),
        }
        client = self

        class Api:
            @staticmethod
            def containers(all=False):
                return [_summary(a["Id"], a["Name"].lstrip("/"), a["Config"]["Labels"])
                        for a in client.all.values()]

            @staticmethod
            def inspect_container(cid):
                return client.all[cid]

        class Containers:
            @staticmethod
            def get(cid):
                if cid not in client.all:
                    raise NotFound("gone")
                return FakeContainer(client.all[cid])

        self.api = Api()
        self.containers = Containers()

    def info(self):
        return {"ServerVersion": "28.1.1", "OperatingSystem": "Ubuntu 24.04", "NCPU": 8,
                "MemTotal": 32 * 2**30, "Containers": 42, "Name": "secret-hostname"}

    def version(self):
        return {"Version": "28.1.1", "ApiVersion": "1.49"}


@pytest.fixture()
def client():
    container_reading._online_runs.clear()
    return TestClient(observer.create_app(FakeClient()))


def test_only_our_containers_are_listed(client):
    names = {c["name"] for c in client.get("/_rsm/containers").json()}
    assert names == {"team2-manager", "team2-docker-gate", "team2-instance-1"}


def test_a_listed_container_carries_no_environment_command_or_mounts(client):
    body = client.get("/_rsm/containers").text
    assert SECRET not in body
    assert "ADMIN_PASSWORD" not in body
    assert "--secret-flag" not in body
    assert "/srv/secret" not in body
    # Only our own labels, not the image's.
    assert "org.opencontainers" not in body
    manager = next(c for c in json.loads(body) if c["name"] == "team2-manager")
    assert manager["stack"] == "team2"
    assert manager["labels"][stacks.LABEL_WEB_PORT] == "7781"
    assert manager["ports"] == [{"port": "8080/tcp", "host_ip": "127.0.0.1", "host_port": "7780"}]
    assert manager["state"]["running"] is True
    gate = next(c for c in json.loads(body) if c["name"] == "team2-docker-gate")
    assert gate["stack"] == "team2"  # from its name: it has no labels


def test_a_labelled_gate_takes_its_stack_from_its_name():
    """The compose file labels a gate with its role only: a stack label would
    make it one of the stack's own containers, visible to the manager."""
    gate = observer.project(_attrs(ID_GATE, "team2-docker-gate", {stacks.LABEL_ROLE: "gate"}))
    assert gate["stack"] == "team2"
    assert gate["labels"] == {stacks.LABEL_ROLE: "gate"}
    assert not stacks.owns({stacks.LABEL_ROLE: "gate"}, "team2")
    assert not stacks.owns({stacks.LABEL_ROLE: "gate"}, stacks.DEFAULT_STACK)


def test_a_container_from_before_stacks_belongs_to_the_default_stack():
    assert observer.stack_of(LEGACY_SERVER) == stacks.DEFAULT_STACK
    assert observer.stack_of({stacks.LABEL_ROLE: "instance"}) is None
    assert observer.stack_of({stacks.LABEL_STACK: "team2"}) == "team2"


def test_a_server_reports_figures_not_its_log(client):
    resp = client.get(f"/_rsm/containers/{ID_SERVER}/server")
    assert resp.status_code == 200
    assert resp.json() == {"server_state": "online", "players": 3, "server_fps": 59.9}
    assert "Alice" not in resp.text and "198.51.100.7" not in resp.text


def test_only_game_servers_have_server_figures(client):
    assert client.get(f"/_rsm/containers/{ID_MANAGER}/server").status_code == 404


def test_other_peoples_containers_do_not_exist(client):
    for ref in (ID_OTHER, ID_LOOKALIKE, "f" * 64):
        assert client.get(f"/_rsm/containers/{ref}/stats").status_code == 404
        assert client.get(f"/_rsm/containers/{ref}/server").status_code == 404


@pytest.mark.parametrize("ref", ["team2-manager", "../../info", "AAAA", "c" * 11])
def test_containers_are_asked_for_by_id_only(client, ref):
    # 403 when the path no longer even looks like a container's.
    assert client.get(f"/_rsm/containers/{ref}/stats").status_code in (403, 404)


def test_stats_are_numbers(client):
    assert client.get(f"/_rsm/containers/{ID_SERVER}/stats").json() == {
        "cpu_percent": 10.0, "mem_bytes": 1024, "mem_limit_bytes": 4096,
    }


def test_the_host_without_its_name_or_counts(client):
    host = client.get("/_rsm/host").json()
    assert host["docker_version"] == "28.1.1"
    assert host["ncpu"] == 8
    assert "secret-hostname" not in json.dumps(host)
    assert 42 not in host.values()


@pytest.mark.parametrize("method,path", [
    ("POST", f"/_rsm/containers/{ID_SERVER}/stats"),
    ("POST", "/containers/create"),
    ("GET", "/containers/json"),
    ("GET", f"/containers/{ID_SERVER}/json"),
    ("DELETE", f"/containers/{ID_SERVER}"),
    ("GET", "/info"),
])
def test_nothing_else_is_answered(client, method, path):
    assert client.request(method, path).status_code in (403, 405)


def test_it_says_what_it_is(client):
    assert client.get("/_rsm/gate").json()["mode"] == "observer"
