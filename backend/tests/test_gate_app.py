"""The Docker gate end to end, in front of a fake Docker daemon (#204).

The fake records every request that reaches it, so the tests can say both what
the client got back and what the daemon was — or was never — asked.
"""
import json

import httpx
import pytest
from starlette.testclient import TestClient

import stacks
from gate import policy
from gate.app import create_app

SERVER = "ghcr.io/acemod/arma-reforger:latest"
HELPER = "steamcmd/steamcmd:latest"
V = "/v1.44"

MINE = {stacks.LABEL_STACK: "team2", stacks.LABEL_MANAGED: "true"}
THEIRS = {stacks.LABEL_STACK: "team1", stacks.LABEL_MANAGED: "true"}
COMPOSE = {stacks.LABEL_STACK: "team2"}  # the manager's own container


class FakeDocker:
    def __init__(self):
        self.containers = {
            "team2-instance-1": MINE,
            "team1-instance-1": THEIRS,
            "team2-manager": COMPOSE,
        }
        self.seen: list[tuple[str, str, bytes]] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("no daemon")
        path = request.url.path
        self.seen.append((request.method, path, request.content))
        if path.endswith("/containers/json"):
            return httpx.Response(200, json=[
                {"Id": name, "Names": [f"/{name}"], "Labels": labels}
                for name, labels in self.containers.items()
            ])
        if path.endswith("/info"):
            return httpx.Response(200, json={"OperatingSystem": "Ubuntu", "Containers": 3})
        if path.endswith("/containers/create"):
            return httpx.Response(201, json={"Id": "new"})
        if "/containers/" in path:
            ref = path.split("/containers/", 1)[1].split("/")[0]
            if ref not in self.containers:
                return httpx.Response(404, json={"message": f"No such container: {ref}"})
            if path.endswith("/json"):
                return httpx.Response(200, json={"Id": ref, "Config": {"Labels": self.containers[ref]}})
            if path.endswith("/logs"):
                return httpx.Response(200, content=b"line 1\nline 2\n")
            return httpx.Response(204)
        if "/networks/" in path:
            return httpx.Response(200, json={"Name": path.rsplit("/", 1)[1]})
        if path.endswith("/images/create"):
            return httpx.Response(200, content=b'{"status":"Pulling"}\n')
        if path.endswith("/_ping"):
            return httpx.Response(200, text="OK")
        return httpx.Response(200, json={})

    def asked(self, method: str, fragment: str) -> bool:
        return any(m == method and fragment in p for m, p, _ in self.seen)


@pytest.fixture()
def gate():
    fake = FakeDocker()
    scope = policy.Scope(
        stack="team2",
        images=frozenset({SERVER, HELPER}),
        host_network_images=frozenset({SERVER}),
        networks=frozenset({"team2-net"}),
        bind_roots=("/opt/team2/data",),
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    with TestClient(create_app(scope, upstream)) as client:
        yield client, fake


def test_describes_itself(gate):
    client, _ = gate
    assert client.get("/_rsm/gate").json()["stack"] == "team2"


def test_ping_passes_through(gate):
    client, fake = gate
    assert client.get(f"{V}/_ping").text == "OK"
    assert fake.asked("GET", "/_ping")


def test_list_shows_only_this_stack(gate):
    client, _ = gate
    names = [c["Id"] for c in client.get(f"{V}/containers/json?all=1").json()]
    assert names == ["team2-instance-1", "team2-manager"]


def test_info_is_trimmed(gate):
    client, _ = gate
    assert client.get(f"{V}/info").json() == {"OperatingSystem": "Ubuntu"}


def test_own_container_is_inspected_stopped_and_removed(gate):
    client, fake = gate
    assert client.get(f"{V}/containers/team2-instance-1/json").json()["Id"] == "team2-instance-1"
    assert client.post(f"{V}/containers/team2-instance-1/stop").status_code == 204
    assert client.delete(f"{V}/containers/team2-instance-1?force=1").status_code == 204
    assert fake.asked("POST", "/containers/team2-instance-1/stop")
    assert fake.asked("DELETE", "/containers/team2-instance-1")


@pytest.mark.parametrize("method,suffix", [
    ("GET", "/json"), ("GET", "/logs"), ("POST", "/stop"), ("POST", "/start"), ("DELETE", ""),
])
def test_another_stacks_container_does_not_exist(gate, method, suffix):
    client, fake = gate
    resp = client.request(method, f"{V}/containers/team1-instance-1{suffix}")
    assert resp.status_code == 404
    assert resp.json()["message"] == "No such container: team1-instance-1"
    # Only the ownership lookup reached Docker, never the action itself.
    assert not fake.asked(method, f"/containers/team1-instance-1{suffix}") or suffix == "/json"


def test_the_managers_own_container_is_readable_but_not_writable(gate):
    client, fake = gate
    assert client.get(f"{V}/containers/team2-manager/json").status_code == 200
    assert client.post(f"{V}/containers/team2-manager/stop").status_code == 404
    assert not fake.asked("POST", "/containers/team2-manager/stop")


def test_logs_are_streamed_through(gate):
    client, _ = gate
    resp = client.get(f"{V}/containers/team2-instance-1/logs?stdout=1&tail=10")
    assert resp.content == b"line 1\nline 2\n"


def test_create_is_stamped_with_the_stack(gate):
    client, fake = gate
    body = {"Image": SERVER, "HostConfig": {"Binds": ["/opt/team2/data/i/1:/x:rw"],
                                            "NetworkMode": "host"}}
    resp = client.post(f"{V}/containers/create?name=team2-instance-2", json=body)
    assert resp.status_code == 201
    sent = json.loads(next(c for m, p, c in fake.seen if p.endswith("/containers/create")))
    assert sent["Labels"][stacks.LABEL_STACK] == "team2"


def test_a_refused_create_never_reaches_docker(gate):
    client, fake = gate
    body = {"Image": SERVER, "HostConfig": {"Privileged": True}}
    resp = client.post(f"{V}/containers/create?name=team2-x", json=body)
    assert resp.status_code == 403
    assert "rsm-gate" in resp.json()["message"]
    assert not fake.asked("POST", "/containers/create")


def test_a_start_never_carries_a_body(gate):
    # Older daemons still read a HostConfig from a start request's body.
    client, fake = gate
    resp = client.post(f"{V}/containers/team2-instance-1/start",
                       json={"Privileged": True, "Binds": ["/:/host"]})
    assert resp.status_code == 204
    sent = next(c for m, p, c in fake.seen if p.endswith("/containers/team2-instance-1/start"))
    assert sent == b""


def test_a_pull_naming_its_tag_twice_is_refused(gate):
    client, fake = gate
    resp = client.post(f"{V}/images/create?fromImage=steamcmd/steamcmd:evil&tag=latest")
    assert resp.status_code == 403
    assert not fake.asked("POST", "/images/create")


def test_a_create_that_is_not_json_is_refused(gate):
    client, _ = gate
    resp = client.post(f"{V}/containers/create", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert resp.status_code == 403


def test_update_is_restart_policy_only(gate):
    client, fake = gate
    ok = client.post(f"{V}/containers/team2-instance-1/update",
                     json={"RestartPolicy": {"Name": "no"}})
    assert ok.status_code == 204
    bad = client.post(f"{V}/containers/team2-instance-1/update",
                      json={"RestartPolicy": {"Name": "no"}, "Memory": 1})
    assert bad.status_code == 403


def test_pull_only_the_stacks_images(gate):
    client, fake = gate
    ok = client.post(f"{V}/images/create?fromImage=steamcmd/steamcmd&tag=latest")
    assert ok.status_code == 200 and b"Pulling" in ok.content
    bad = client.post(f"{V}/images/create?fromImage=alpine&tag=latest")
    assert bad.status_code == 403
    assert not fake.asked("POST", "/images/create") or len(
        [1 for m, p, _ in fake.seen if p.endswith("/images/create")]) == 1


def test_image_inspect_only_the_stacks_images(gate):
    client, _ = gate
    assert client.get(f"{V}/images/ghcr.io/acemod/arma-reforger:latest/json").status_code == 200
    assert client.get(f"{V}/images/alpine:latest/json").status_code == 403


def test_only_the_stacks_network(gate):
    client, _ = gate
    assert client.get(f"{V}/networks/team2-net").status_code == 200
    assert client.get(f"{V}/networks/team1-net").status_code == 404


@pytest.mark.parametrize("method,path", [
    ("POST", f"{V}/containers/team2-instance-1/exec"),
    ("GET", f"{V}/volumes"),
    ("POST", f"{V}/build"),
])
def test_refused_paths_never_reach_docker(gate, method, path):
    client, fake = gate
    assert client.request(method, path).status_code == 403
    assert fake.seen == []


def test_docker_down_is_a_502(gate):
    client, fake = gate
    fake.down = True
    resp = client.get(f"{V}/containers/json")
    assert resp.status_code == 502
