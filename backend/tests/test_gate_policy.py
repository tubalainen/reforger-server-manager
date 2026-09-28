"""The Docker gate's rules (#204): what a stack may ask of the Docker API."""
import pytest

import stacks
from gate import policy
from gate.policy import Denied

SERVER = "ghcr.io/acemod/arma-reforger:latest"
HELPER = "steamcmd/steamcmd:latest"

SCOPE = policy.Scope(
    stack="team2",
    images=frozenset({SERVER, HELPER}),
    host_network_images=frozenset({SERVER}),
    networks=frozenset({"team2-net"}),
    bind_roots=("/opt/rsm-team2/data", "/opt/rsm-team2/serverfiles/stable"),
)


def _instance_body(**host):
    """Roughly what docker-py sends for a game server (instance_service)."""
    return {
        "Image": SERVER,
        "Env": ["SERVER_BIND_PORT=2003"],
        "Labels": {"reforger-manager.role": "instance"},
        "HostConfig": {
            "Binds": [
                "/opt/rsm-team2/serverfiles/stable:/reforger:rw",
                "/opt/rsm-team2/data/instances/1/profile:/home/profile:rw",
            ],
            "NetworkMode": "host",
            "RestartPolicy": {"Name": "unless-stopped"},
            **host,
        },
    }


# --------------------------------------------------------------------------- #
# route
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method,path,kind,write", [
    ("GET", "/_ping", "ping", False),
    ("HEAD", "/v1.44/_ping", "ping", False),
    ("GET", "/version", "version", False),
    ("GET", "/v1.55/info", "info", False),
    ("GET", "/_rsm/gate", "gate", False),
    ("GET", "/v1.44/containers/json", "list", False),
    ("POST", "/v1.44/containers/create", "create", True),
    ("GET", "/v1.44/containers/abc123/json", "container", False),
    ("GET", "/v1.44/containers/team2-instance-1/logs", "container", False),
    ("POST", "/v1.44/containers/abc123/stop", "container", True),
    ("POST", "/v1.44/containers/abc123/update", "container", True),
    ("DELETE", "/v1.44/containers/abc123", "container", True),
    ("GET", "/v1.44/images/ghcr.io/acemod/arma-reforger:latest/json", "image", False),
    ("GET", "/v1.44/images/repo%40sha256:abc/json", "image", False),
    ("POST", "/v1.44/images/create", "pull", True),
    ("GET", "/v1.44/networks/team2-net", "network", False),
])
def test_allowed_routes(method, path, kind, write):
    r = policy.route(method, path)
    assert (r.kind, r.write) == (kind, write)


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1.44/containers/abc/exec"),        # a shell in any container
    ("POST", "/v1.44/exec/abc/start"),
    ("GET", "/v1.44/containers/abc/archive"),      # copy files out
    ("PUT", "/v1.44/containers/abc/archive"),      # copy files in
    ("GET", "/v1.44/containers/abc/export"),
    ("POST", "/v1.44/containers/abc/attach"),
    ("POST", "/v1.44/containers/abc/rename"),
    ("POST", "/v1.44/containers/prune"),
    ("GET", "/v1.44/volumes"),
    ("POST", "/v1.44/volumes/create"),
    ("POST", "/v1.44/build"),
    ("POST", "/v1.44/commit"),
    ("DELETE", "/v1.44/images/busybox"),
    ("GET", "/v1.44/images/json"),
    ("GET", "/v1.44/networks"),
    ("POST", "/v1.44/networks/create"),
    ("GET", "/v1.44/events"),
    ("GET", "/v1.44/system/df"),
    ("POST", "/v1.44/swarm/init"),
    ("GET", "/v1.44/containers/../images/json"),
    ("GET", "//v1.44/containers/json"),
    ("GET", "/v1.44/containers/a%2fb/json"),        # an escaped slash in a name
    ("GET", "/v1.44/containers/abc/json/extra"),
])
def test_everything_else_is_refused(method, path):
    with pytest.raises(Denied):
        policy.route(method, path)


# --------------------------------------------------------------------------- #
# container create
# --------------------------------------------------------------------------- #
def test_a_game_server_passes_and_is_stamped_with_the_stack():
    body = policy.check_create(_instance_body(), "team2-instance-1", SCOPE)
    assert body["Labels"][stacks.LABEL_STACK] == "team2"
    assert body["Labels"][stacks.LABEL_MANAGED] == "true"
    assert body["Labels"]["reforger-manager.role"] == "instance"  # kept


def test_a_client_cannot_claim_another_stack():
    sneaky = _instance_body()
    sneaky["Labels"] = {stacks.LABEL_STACK: "reforger", stacks.LABEL_MANAGED: "false"}
    body = policy.check_create(sneaky, "team2-instance-1", SCOPE)
    assert body["Labels"][stacks.LABEL_STACK] == "team2"
    assert body["Labels"][stacks.LABEL_MANAGED] == "true"


def test_unnamed_helper_containers_are_fine():
    helper = {
        "Image": HELPER,
        "HostConfig": {
            "Binds": ["/opt/rsm-team2/data/instances:/idata:rw"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
    }
    assert policy.check_create(helper, None, SCOPE)["Labels"][stacks.LABEL_STACK] == "team2"


@pytest.mark.parametrize("name", ["reforger-instance-1", "instance-1", "team22-x", "team2"])
def test_names_must_carry_the_stack_prefix(name):
    with pytest.raises(Denied, match="must start with 'team2-'"):
        policy.check_create(_instance_body(), name, SCOPE)


@pytest.mark.parametrize("image", ["alpine:latest", "docker:dind", "ghcr.io/acemod/arma-reforger:other"])
def test_only_the_stacks_images(image):
    body = _instance_body()
    body["Image"] = image
    with pytest.raises(Denied, match="not one this stack runs"):
        policy.check_create(body, None, SCOPE)


@pytest.mark.parametrize("host", [
    {"Privileged": True},
    {"CapAdd": ["SYS_ADMIN"]},
    {"Devices": [{"PathOnHost": "/dev/sda"}]},
    {"DeviceRequests": [{"Driver": "nvidia"}]},
    {"VolumesFrom": ["team1-instance-1"]},
    {"Sysctls": {"kernel.core_pattern": "|/x"}},
    {"CgroupParent": "/"},
    {"ReadonlyPaths": []},          # empty = unmask
    {"MaskedPaths": []},
    {"PidMode": "host"},
    {"IpcMode": "host"},
    {"UTSMode": "host"},
    {"UsernsMode": "host"},
    {"CgroupnsMode": "host"},
    {"PidMode": "container:team1-instance-1"},
    {"SecurityOpt": ["apparmor=unconfined"]},
    {"SecurityOpt": ["seccomp=unconfined"]},
    {"SecurityOpt": ["label=disable"]},
    {"Runtime": "sysbox-runc"},
])
def test_anything_that_hands_over_the_host_is_refused(host):
    with pytest.raises(Denied):
        policy.check_create(_instance_body(**host), None, SCOPE)


@pytest.mark.parametrize("binds", [
    ["/:/host"],
    ["/var/run/docker.sock:/var/run/docker.sock"],
    ["/opt/rsm-team1/data:/data"],                       # the other team's folder
    ["/opt/rsm-team2/data/../../rsm-team1/data:/data"],  # ...reached with ..
    ["/opt/rsm-team2/database:/x"],                      # a prefix, not a subfolder
    ["team1-data:/data"],                                # a named volume
])
def test_binds_stay_inside_the_stacks_folders(binds):
    with pytest.raises(Denied, match="outside this stack's folders"):
        policy.check_create(_instance_body(Binds=binds), None, SCOPE)


def test_mounts_are_checked_like_binds():
    ok = _instance_body(Mounts=[
        {"Type": "bind", "Source": "/opt/rsm-team2/data/x", "Target": "/x"},
        {"Type": "tmpfs", "Target": "/tmp"},
    ])
    policy.check_create(ok, None, SCOPE)
    for bad in (
        {"Type": "bind", "Source": "/etc", "Target": "/x"},
        {"Type": "volume", "Source": "team1-data", "Target": "/x"},
    ):
        with pytest.raises(Denied):
            policy.check_create(_instance_body(Mounts=[bad]), None, SCOPE)


def test_no_folders_known_means_no_binds_at_all():
    blind = policy.Scope("team2", SCOPE.images, SCOPE.host_network_images, SCOPE.networks, ())
    with pytest.raises(Denied):
        policy.check_create(_instance_body(), None, blind)


def test_host_networking_is_for_the_game_server_only():
    helper = {"Image": HELPER, "HostConfig": {"NetworkMode": "host"}}
    with pytest.raises(Denied, match="host networking"):
        policy.check_create(helper, None, SCOPE)


def test_only_the_stacks_network():
    policy.check_create(_instance_body(NetworkMode="team2-net"), None, SCOPE)
    policy.check_create(_instance_body(NetworkMode="default"), None, SCOPE)
    for mode in ("reforger-net", "team1-net", "container:team1-manager"):
        with pytest.raises(Denied):
            policy.check_create(_instance_body(NetworkMode=mode), None, SCOPE)
    body = _instance_body(NetworkMode="team2-net")
    body["NetworkingConfig"] = {"EndpointsConfig": {"team1-net": {}}}
    with pytest.raises(Denied, match="team1-net"):
        policy.check_create(body, None, SCOPE)


def test_malformed_bodies_are_refused():
    for body in (None, [], "x"):
        with pytest.raises(Denied):
            policy.check_create(body, None, SCOPE)
    with pytest.raises(Denied):
        policy.check_create({"Image": SERVER, "HostConfig": []}, None, SCOPE)
    with pytest.raises(Denied):
        policy.check_create({"Image": SERVER, "Labels": ["a=b"]}, None, SCOPE)


# --------------------------------------------------------------------------- #
# update, images, filtering
# --------------------------------------------------------------------------- #
def test_update_may_only_change_the_restart_policy():
    assert policy.check_update({"RestartPolicy": {"Name": "no"}})
    for body in ({"RestartPolicy": {"Name": "no"}, "Memory": 1}, {"Devices": []}, None):
        with pytest.raises(Denied):
            policy.check_update(body)


@pytest.mark.parametrize("ref,tag,ok", [
    ("ghcr.io/acemod/arma-reforger", "latest", True),
    ("ghcr.io/acemod/arma-reforger:latest", None, True),
    ("steamcmd/steamcmd", None, True),               # no tag = latest
    ("steamcmd/steamcmd", "beta", False),
    ("alpine", "latest", False),
])
def test_image_allowed(ref, tag, ok):
    assert policy.image_allowed(SCOPE, ref, tag) is ok


def test_image_pinned_by_digest():
    digest = "sha256:" + "a" * 64
    pinned = policy.Scope("team2", frozenset({f"ghcr.io/acemod/arma-reforger@{digest}"}),
                          frozenset(), frozenset(), ())
    assert policy.image_allowed(pinned, "ghcr.io/acemod/arma-reforger", digest)
    assert policy.image_allowed(pinned, f"ghcr.io/acemod/arma-reforger@{digest}")
    assert not policy.image_allowed(pinned, "ghcr.io/acemod/arma-reforger", "latest")


def _summary(labels):
    return {"Id": "x", "Labels": labels}


def test_list_shows_only_the_stacks_containers():
    listed = [
        _summary({stacks.LABEL_STACK: "team2", stacks.LABEL_MANAGED: "true"}),
        _summary({stacks.LABEL_STACK: "reforger", stacks.LABEL_MANAGED: "true"}),
        _summary({stacks.LABEL_MANAGED: "true"}),       # pre-v0.65.0
        _summary({}),                                   # not ours at all
        _summary(None),
    ]
    assert policy.filter_list(listed, SCOPE) == [listed[0]]
    default = policy.Scope("reforger", SCOPE.images, frozenset(), frozenset(), ())
    assert policy.filter_list(listed, default) == [listed[1], listed[2]]


def test_info_loses_the_host_wide_counts():
    info = {"OperatingSystem": "Ubuntu 24.04", "NCPU": 8, "Containers": 40,
            "ContainersRunning": 12, "Images": 90, "Name": "docker06"}
    assert policy.filter_info(info) == {"OperatingSystem": "Ubuntu 24.04", "NCPU": 8}


# --------------------------------------------------------------------------- #
# stacks.owns and the stack name
# --------------------------------------------------------------------------- #
def test_owns():
    mine = {stacks.LABEL_STACK: "team2", stacks.LABEL_MANAGED: "true"}
    compose = {stacks.LABEL_STACK: "team2"}  # the manager's own container
    legacy = {stacks.LABEL_MANAGED: "true"}
    assert stacks.owns(mine, "team2", write=True)
    assert stacks.owns(compose, "team2") and not stacks.owns(compose, "team2", write=True)
    assert stacks.owns(legacy, "reforger", write=True)
    assert not stacks.owns(legacy, "team2")
    assert not stacks.owns({}, "reforger")
    assert not stacks.owns(mine, "reforger")


@pytest.mark.parametrize("name,ok", [
    ("reforger", True), ("team2", True), ("a", True), ("milsim-eu-1", True),
    ("", False), ("Team2", False), ("-team", False), ("team_2", False),
    ("team 2", False), ("a" * 32, False), ("team2/../x", False),
])
def test_stack_names(name, ok):
    assert (stacks.stack_name_error(name) is None) is ok
