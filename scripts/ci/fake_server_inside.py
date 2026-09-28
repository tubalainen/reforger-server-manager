"""Start a stand-in game server for the Supervisor to see, INSIDE a manager (#204).

    docker exec -i -u app -e PLAYERS=3 reforger-manager python - < scripts/ci/fake_server_inside.py

Created through this stack's Docker gate with the labels the manager gives a
real server, from the stack's server image (a small stand-in in CI), and it
prints the lines a Reforger server prints once it is up — then sleeps. The CI
job 'gate-e2e' checks the Supervisor reports it with its name and players.
"""
import os
import sys

import docker

sys.path.insert(0, "/app")
import config  # noqa: E402
import stacks  # noqa: E402
from services import docker_service  # noqa: E402

players = int(os.environ.get("PLAYERS", "3"))
name = docker_service.container_name("instance-99")
client = docker.from_env()
try:
    client.containers.get(name).remove(force=True)
except docker.errors.NotFound:
    pass

# A trap, or `docker stop` would wait out its timeout: PID 1 ignores SIGTERM.
log = ("trap 'exit 0' TERM; "
       "echo 'DEFAULT      : Entered online game state.'; "
       f"echo 'FPS: 60.0, frame time (avg: 16.7 ms), Mem: 1190747 kB, Player: {players}, AI: 0'; "
       "sleep 3600 & wait")
client.images.pull(config.settings.reforger_server_image)  # create does not pull
container = client.containers.create(
    config.settings.reforger_server_image, ["sh", "-c", log],
    name=name,
    labels=docker_service.managed_labels(**{
        stacks.LABEL_ROLE: stacks.ROLE_INSTANCE,
        stacks.LABEL_BRANCH: "stable",
        stacks.LABEL_INSTANCE_ID: "99",
        stacks.LABEL_NAME: f"{config.settings.rsm_stack} e2e server",
        stacks.LABEL_MAX_PLAYERS: "64",
        stacks.LABEL_GAME_PORT: str(config.settings.game_port_range[0]),
        stacks.LABEL_A2S_PORT: str(config.settings.a2s_port_range[0]),
        stacks.LABEL_RCON_PORT: str(config.settings.rcon_port_range[0]),
    }),
)
container.start()
print(f"started {name} ({container.id[:12]})")
