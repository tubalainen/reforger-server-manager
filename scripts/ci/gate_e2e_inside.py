"""End-to-end check of one stack's Docker gate, run INSIDE its manager (#204).

    docker exec -i -u app -e OTHER_STACK=team2 reforger-manager \
        python - < scripts/ci/gate_e2e_inside.py

It talks to Docker exactly the way the manager does — DOCKER_HOST, the gate's
unix socket, as the unprivileged app user — and checks both halves of the
gate's job: what this stack needs goes through, and everything that would reach
the host or another stack is refused. Exits non-zero on any failure. The CI job
'gate-e2e' runs it once from each of two stacks on one Docker host.
"""
import os
import sys

import docker
from docker.errors import APIError, NotFound

sys.path.insert(0, "/app")
import config  # noqa: E402
from services import docker_service  # noqa: E402

STACK = config.settings.rsm_stack
OTHER = os.environ["OTHER_STACK"]
HELPER = config.settings.steamcmd_image
NOT_OURS = "hello-world:latest"

client = docker.from_env()
failures: list[str] = []


def check(name: str, ok: bool) -> None:
    print(("PASS  " if ok else "FAIL  ") + name, flush=True)
    if not ok:
        failures.append(name)


def refused(name: str, call, status: int = 403) -> None:
    """`call` must be answered with an error of this status, and nothing else."""
    try:
        result = call()
    except APIError as exc:
        check(f"{name} -> {exc.status_code}", exc.status_code == status)
        return
    check(f"{name} -> was let through ({result!r})", False)


print(f"== Docker gate of stack '{STACK}' (other stack: '{OTHER}')", flush=True)

# --- The manager knows it is behind its own gate ------------------------------
info = docker_service.gate_info() or {}
check("the gate describes itself", info.get("gate") == "rsm" and info.get("stack") == STACK)
check("no gate warning in the GUI", docker_service.gate_warning() is None)
check("no Docker API exposure warning", docker_service.docker_api_exposure() is None)

# --- What this stack needs goes through ---------------------------------------
listed = client.containers.list(all=True)
names = {c.name for c in listed}
check("the list shows this stack's manager", f"{STACK}-manager" in names)
check(
    "the list shows only this stack",
    all(c.labels.get("reforger-manager.stack") == STACK for c in listed),
)
own = client.containers.get(f"{STACK}-manager")
check("the manager can inspect itself", own.labels.get("reforger-manager.stack") == STACK)

data_host = docker_service.host_path_for("/data")
check(f"/data maps to a host path ({data_host})", data_host.startswith("/") and data_host != "/data")

try:
    client.images.pull(HELPER)
    check(f"pull the helper image {HELPER}", True)
except APIError as exc:
    check(f"pull the helper image {HELPER} ({exc})", False)

helper = None
try:
    helper = client.containers.create(
        HELPER, ["true"], name=docker_service.container_name("e2e-helper"),
        volumes={data_host: {"bind": "/d", "mode": "ro"}},
        labels={"reforger-manager.role": "e2e"},  # no stack label: the gate adds it
    )
    labels = helper.labels
    check("create a helper that mounts this stack's data", True)
    check("the gate stamped it with the stack",
          labels.get("reforger-manager.stack") == STACK
          and labels.get("reforger-manager.managed") == "true")
    helper.start()
    check("start it", helper.wait(timeout=60).get("StatusCode") == 0)
except APIError as exc:
    check(f"create/start a helper ({exc})", False)
finally:
    if helper is not None:
        try:
            helper.remove(force=True)
            check("remove it", True)
        except APIError as exc:
            check(f"remove it ({exc})", False)

check("inspect this stack's network", client.networks.get(f"{STACK}-net").name == f"{STACK}-net")
daemon = client.info()
check("docker info answers, without host-wide counts",
      "ServerVersion" in daemon and "Containers" not in daemon)

# --- Other stacks do not exist --------------------------------------------------
for ref in (f"{OTHER}-manager", f"{OTHER}-docker-gate", f"{STACK}-docker-gate"):
    try:
        client.containers.get(ref)
        check(f"{ref} is invisible", False)
    except NotFound:
        check(f"{ref} is invisible", True)
try:
    client.networks.get(f"{OTHER}-net")
    check(f"{OTHER}-net is invisible", False)
except NotFound:
    check(f"{OTHER}-net is invisible", True)

# --- The manager's own container is readable, not writable --------------------
refused("change the manager's own restart policy",
        lambda: client.api.update_container(own.id, restart_policy={"Name": "no"}), 404)
refused("exec into the manager", lambda: client.api.exec_create(own.id, "true"))

# --- Nothing that reaches the host ---------------------------------------------
refused("create a privileged container",
        lambda: client.containers.create(HELPER, privileged=True))
refused("mount the host's root", lambda: client.containers.create(
    HELPER, volumes={"/": {"bind": "/host", "mode": "rw"}}))
refused("mount the Docker socket", lambda: client.containers.create(
    HELPER, volumes={"/var/run/docker.sock": {"bind": "/s", "mode": "rw"}}))
refused("mount a named volume", lambda: client.containers.create(
    HELPER, volumes={f"{OTHER}-docker-gate": {"bind": "/g", "mode": "rw"}}))
refused("host networking for a helper",
        lambda: client.containers.create(HELPER, network_mode="host"))
refused("the host's process namespace",
        lambda: client.containers.create(HELPER, pid_mode="host"))
refused("an added capability", lambda: client.containers.create(HELPER, cap_add=["SYS_ADMIN"]))
refused("an image this stack does not run", lambda: client.containers.create(NOT_OURS))
refused("pull an image this stack does not run", lambda: client.images.pull(NOT_OURS))
refused("a name in another stack", lambda: client.containers.create(
    HELPER, name=f"{OTHER}-sneaky"))
refused("join another stack's network", lambda: client.containers.create(
    HELPER, network=f"{OTHER}-net"))
refused("create a volume", lambda: client.volumes.create(f"{STACK}-e2e"))
build = client.api.post(client.api._url("/build"), data=b"")
check(f"build an image -> {build.status_code}", build.status_code == 403)

print(f"== {len(failures)} failure(s)", flush=True)
sys.exit(1 if failures else 0)
