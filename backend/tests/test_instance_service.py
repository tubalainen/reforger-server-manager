import json
from datetime import UTC, datetime

import pytest

from models import Instance
from services import instance_service
from services.template_service import TemplateSpec, render_config_json


def _inst(**over):
    base = dict(id=1, name="srv", template_id=1, branch="stable",
                game_port=2005, a2s_port=17780, rcon_port=20002)
    base.update(over)
    return Instance(**base)


def _template_config(**over):
    spec = TemplateSpec(
        name="t", scenario_id="{ABC}Missions/x.conf",
        mods=[{"modId": "AAA", "name": "A", "version": "1.0"}],
        rcon_password="secret", **over,
    )
    return render_config_json(spec)


def test_render_bakes_instance_ports():
    cfg = instance_service.render_instance_config(_template_config(), _inst(), "203.0.113.5")
    assert cfg["bindPort"] == 2005
    assert cfg["publicPort"] == 2005
    assert cfg["publicAddress"] == "203.0.113.5"
    assert cfg["a2s"]["port"] == 17780
    assert cfg["rcon"]["port"] == 20002


def test_render_without_public_address_leaves_it():
    cfg = instance_service.render_instance_config(_template_config(), _inst(), "")
    # empty public address must not overwrite the template's value
    assert "publicAddress" not in cfg or cfg["publicAddress"] == ""


def test_restart_policy_mapping():
    assert instance_service._restart_policy(_inst(auto_start=True, auto_restart=True)) == "unless-stopped"
    assert instance_service._restart_policy(_inst(auto_start=True, auto_restart=False)) == "unless-stopped"
    assert instance_service._restart_policy(_inst(auto_start=False, auto_restart=True)) == "on-failure"
    assert instance_service._restart_policy(_inst(auto_start=False, auto_restart=False)) == "no"


def test_parse_server_status_reads_latest_line():
    log = (
        "some boot noise\n"
        "FPS: 30.0, frame time (avg: 33 ms), Mem: 900000 kB, Player: 1, AI: 0\n"
        "more noise\n"
        "FPS: 59.9, frame time (avg: 16.7 ms), Mem: 1190747 kB, Player: 12, AI: 40, Veh: 3\n"
    )
    s = instance_service.parse_server_status(log)
    assert s == {"fps": 59.9, "mem_kb": 1190747, "players": 12}


def test_parse_server_status_none_when_absent():
    assert instance_service.parse_server_status("no status here") is None


def test_parse_server_status_tolerates_variants():
    # plural "Players:", reordered fields, and a line with FPS but no player/mem
    log = (
        "FPS: 20.0 (starting up)\n"  # newer line, FPS only -> mem/players None
    )
    s = instance_service.parse_server_status(log)
    assert s == {"fps": 20.0, "mem_kb": None, "players": None}

    log2 = "Player: 3, Mem: 500000 kB, FPS: 45.5, AI: 10\n"  # fields reordered
    s2 = instance_service.parse_server_status(log2)
    assert s2 == {"fps": 45.5, "mem_kb": 500000, "players": 3}

    log3 = "FPS: 60.0, Mem: 900000 kB, Players: 7\n"  # plural Players
    assert instance_service.parse_server_status(log3)["players"] == 7


def test_parse_server_status_reads_players_connected_network_line():
    # Real logs: player count is on its own NETWORK line, not the FPS line (#38).
    log = (
        "  NETWORK      : Players connecting: 0\n"
        "  NETWORK      : Players connected: 1 / 1\n"
    )
    s = instance_service.parse_server_status(log)
    assert s == {"fps": None, "mem_kb": None, "players": 1}


def test_parse_server_status_combines_separate_fps_and_player_lines():
    # FPS from a -logStats line, player count from a later NETWORK line: both win.
    log = (
        "DEFAULT : FPS: 58.0, frame time (avg: 17 ms), Mem: 1000000 kB, Player: 0, AI: 0\n"
        "lots of noise\n"
        "  NETWORK      : Players connected: 3 / 3\n"
    )
    s = instance_service.parse_server_status(log)
    assert s == {"fps": 58.0, "mem_kb": 1000000, "players": 3}


def test_parse_public_address_from_registration_line():
    # The real BACKEND registration line reveals the public IP (#46).
    log = (
        "  BACKEND      : Ping Site: frankfurt\n"
        "  BACKEND      : Server registered with address: 203.0.113.7:2001\n"
    )
    assert instance_service.parse_public_address(log) == "203.0.113.7"


def test_parse_public_address_none_when_absent():
    assert instance_service.parse_public_address("no address here") is None


STARTUP_LOG = (
    "  SCRIPT       : Loading mods\n"
    "  RESOURCES    : Loading world\n"
)
ONLINE_LOG = STARTUP_LOG + (
    "  BACKEND      : Server registered with address: 203.0.113.7:2002\n"
    "  BACKEND      : Direct Join Code: 12345\n"
    "  DEFAULT      : Entered online game state.\n"
    "  WORLD        : Frame start\n"
)


def test_parse_server_state_starting_until_the_server_says_it_is_up():
    # A running container is not a joinable server: mods and the world load first (#76).
    assert instance_service.parse_server_state(STARTUP_LOG) == instance_service.STATE_STARTING
    assert instance_service.parse_server_state(ONLINE_LOG) == instance_service.STATE_ONLINE


def test_parse_server_state_online_from_game_state_line_alone():
    # A private (unlisted) server never registers with the backend, but still
    # enters the online game state.
    log = STARTUP_LOG + "  DEFAULT      : Entered online game state.\n"
    assert instance_service.parse_server_state(log) == instance_service.STATE_ONLINE


def test_parse_server_state_online_from_the_periodic_stats_line():
    # On a long-lived server the one-shot startup lines scroll out of the tail we
    # read; the -logStats line keeps proving the world is running.
    log = "  DEFAULT      : FPS: 60.0, Mem: 1190747 kB, Player: 2\n"
    assert instance_service.parse_server_state(log) == instance_service.STATE_ONLINE


class _FakeLogContainer:
    """A container whose logs behave like a real daemon's.

    Lines are (timestamp, text) pairs spanning both runs. `since` is honoured the
    way docker actually honours it: the SDK truncates it to whole seconds, so a
    previous-run line written in the same second the new run began still comes
    through — the case that kept a restarted server looking online (#76).
    """

    def __init__(self, started, lines=()):
        self.id = "cid-1"
        self.attrs = {"State": {"StartedAt": started}}
        self.lines = list(lines)

    def logs(self, tail=None, since=None, timestamps=False):
        out = []
        for ts, text in self.lines:
            if since is not None and ts < since.replace(microsecond=0):
                continue  # docker's second-granularity filter
            out.append(f"{ts.isoformat().replace('+00:00', '')}Z {text}" if timestamps else text)
        return ("\n".join(out) + "\n").encode()


def _ts(second, micro=0):
    return datetime(2026, 7, 14, 10, 0, second, micro, tzinfo=UTC)


def test_current_run_log_ignores_the_previous_run():
    # Docker keeps a container's log across restarts. Without anchoring on
    # StartedAt, a restarting server would look online because the run BEFORE it
    # was (#76) — and its stale FPS/player numbers would be served as current.
    instance_service._online_runs.clear()
    c = _FakeLogContainer(
        started="2026-07-14T10:00:30Z",
        lines=[
            (_ts(10), "  DEFAULT      : Entered online game state."),   # previous run
            (_ts(20), "  DEFAULT      : FPS: 60.0, Player: 5"),          # previous run
            (_ts(31), "  SCRIPT       : Loading mods"),                  # current run
        ],
    )
    log = instance_service.current_run_log(c)
    assert "Entered online game state" not in log
    assert "Loading mods" in log
    assert instance_service.server_state(c, log) == instance_service.STATE_STARTING


def test_current_run_log_drops_a_previous_line_from_the_start_second():
    # The regression behind the #76 follow-up: docker's `since` is truncated to
    # whole seconds, so the dying run's last stats line — written in the same
    # second the new run started — slipped through and reported the freshly
    # restarting server as online. Line timestamps are compared at full precision.
    instance_service._online_runs.clear()
    c = _FakeLogContainer(
        started="2026-07-14T10:00:30.800000Z",
        lines=[
            (_ts(30, 100000), "  DEFAULT      : FPS: 60.0, Player: 5"),  # previous run!
            (_ts(31), "  SCRIPT       : Loading mods"),                  # current run
        ],
    )
    log = instance_service.current_run_log(c)
    assert "FPS" not in log
    assert instance_service.server_state(c, log) == instance_service.STATE_STARTING


def test_restarting_an_instance_forgets_that_the_old_run_was_online():
    # The memo must not outlive the run it describes, however the restart happens.
    instance_service._online_runs.clear()
    c = _FakeLogContainer(started="2026-07-14T10:00:00Z")
    assert instance_service.server_state(c, ONLINE_LOG) == instance_service.STATE_ONLINE

    instance_service.forget_run(c.id)
    assert instance_service.server_state(c, STARTUP_LOG) == instance_service.STATE_STARTING


def test_max_fps_config_echo_is_not_a_live_fps_reading():
    # "maxFPS: 60" in a startup config echo must not read as an FPS sample — nor
    # as proof the world is running, since the stats line doubles as that (#76).
    log = "  ENGINE       : maxFPS: 60\n"
    assert instance_service.parse_server_status(log) is None
    assert instance_service.parse_server_state(log) == instance_service.STATE_STARTING


def test_config_echo_is_never_read_as_live_server_stats():
    # The server logs its own CONFIG too. Unanchored, `Players?:` matches inside
    # "maxPlayers:" — so a config echo reported 64 players on an empty server, and
    # fed that into the summary bar's player total (#85). Same trap as maxFPS.
    log = "  ENGINE       : maxPlayers: 64, maxFPS: 60, maxMem: 8000 kB\n"
    assert instance_service.parse_server_status(log) is None
    assert instance_service.parse_server_state(log) == instance_service.STATE_STARTING

    # ...while the real stats line still parses in full.
    real = "  DEFAULT      : FPS: 59.9, Mem: 1190747 kB, Player: 12\n"
    assert instance_service.parse_server_status(real) == {
        "fps": 59.9, "mem_kb": 1190747, "players": 12,
    }


def test_server_state_stays_online_once_seen_for_that_run():
    # The proof lines eventually scroll away; a server that came up stays up
    # until its container restarts.
    instance_service._online_runs.clear()
    c = _FakeLogContainer(started="2026-07-14T10:00:00Z")
    assert instance_service.server_state(c, ONLINE_LOG) == instance_service.STATE_ONLINE
    assert instance_service.server_state(c, "  WORLD  : nothing telling here\n") == (
        instance_service.STATE_ONLINE
    )
    # ...but a restart (new StartedAt) is a fresh run and must prove itself again.
    c.attrs["State"]["StartedAt"] = "2026-07-14T11:30:00Z"
    assert instance_service.server_state(c, STARTUP_LOG) == instance_service.STATE_STARTING


def test_inject_stats_logging_adds_arg_and_respects_user_override():
    assert instance_service._inject_stats_logging("").startswith("-logStats ")
    # existing args are preserved alongside the injected -logStats
    out = instance_service._inject_stats_logging("-nds 3")
    assert "-logStats" in out and "-nds 3" in out
    # a user-set -logStats is left exactly as-is (no duplicate)
    assert instance_service._inject_stats_logging("-logStats 5000") == "-logStats 5000"


class _FakeEnvContainer:
    def __init__(self, env, fail=False):
        self.attrs = {"Config": {"Env": env}}
        self._fail = fail

    def reload(self):
        if self._fail:
            raise AttributeError("cannot inspect")


def test_container_env_match_detects_stale_launch_params():
    # Docker bakes env at container creation, so a template's launch parameters
    # edited afterwards were silently ignored on a plain restart (#79).
    matches = instance_service._container_env_matches
    desired = {"ARMA_PARAMS": "-logStats 10000 -nds 3", "STEAM_APPID": "1874900"}

    assert matches(_FakeEnvContainer(
        ["ARMA_PARAMS=-logStats 10000 -nds 3", "STEAM_APPID=1874900", "PATH=/usr/bin"]
    ), desired) is True
    # the template's launch params changed -> the container is stale
    assert matches(_FakeEnvContainer(
        ["ARMA_PARAMS=-logStats 10000", "STEAM_APPID=1874900"]
    ), desired) is False
    # still covers the old -logStats check (#38): missing key -> stale
    assert matches(_FakeEnvContainer(["STEAM_APPID=1874900"]), desired) is False
    # uninspectable container -> True, so we never destroy what we can't read
    assert matches(_FakeEnvContainer([], fail=True), desired) is True


def _seed_instance_data(tmp_path, monkeypatch):
    """A realistic on-disk instance: baked mods, a save, logs."""
    from sqlmodel import Session

    import config
    import models

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    with Session(models.get_engine()) as session:
        session.add(_inst(id=1))
        session.commit()
    idir = tmp_path / "instances" / "1"
    (idir / "workshop" / "591AF5BDA9F7CEEB").mkdir(parents=True)
    (idir / "workshop" / "591AF5BDA9F7CEEB" / "addon.pak").write_bytes(b"x" * 1000)
    (idir / "profile" / "save").mkdir(parents=True)
    (idir / "profile" / "save" / "world.bin").write_bytes(b"s" * 100)
    (idir / "profile" / "logs" / "session1").mkdir(parents=True)
    (idir / "profile" / "logs" / "session1" / "console.log").write_bytes(b"l" * 10)
    (idir / "configs").mkdir(parents=True)
    (idir / "configs" / "server.json").write_text("{}")
    return idir


def test_desired_environment_pins_the_save_and_addons_dirs_to_the_mounts():
    # The image only DEFAULTS -profile to /home/profile and the addons dir to
    # /reforger/workshop (acemod launch.py reads ARMA_PROFILE / ARMA_WORKSHOP_DIR).
    # We pin them to the paths we bind-mount: if a future image changed a default,
    # the persistent save (<profile>/.save/game) would be written INSIDE the
    # container and the next container rebuild would silently take it with it (#79).
    env = instance_service._desired_environment(_inst(), None)
    assert env["ARMA_PROFILE"] == instance_service.PROFILE_DIR == "/home/profile"
    assert env["ARMA_WORKSHOP_DIR"] == instance_service.WORKSHOP_DIR == "/reforger/workshop"


def test_the_save_dir_the_server_actually_uses_is_detected(tmp_path, monkeypatch):
    # The real server writes its persistence to <profile>/.save/game.
    from sqlmodel import Session

    import config
    import models

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    with Session(models.get_engine()) as session:
        session.add(_inst(id=1))
        session.commit()
    save = tmp_path / "instances" / "1" / "profile" / ".save" / "game"
    save.mkdir(parents=True)
    (save / "world.bin").write_bytes(b"s" * 512)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: False)

    saves = {i["target"]: i for i in instance_service.instance_data(1)["items"]}["saves"]
    assert saves["paths"] == [".save"]
    assert saves["size_bytes"] == 512
    assert saves["mount"] == instance_service.PROFILE_DIR


def test_instance_data_reports_what_is_on_disk(tmp_path, monkeypatch):
    _seed_instance_data(tmp_path, monkeypatch)
    monkeypatch.setattr(instance_service.docker_service, "ping", lambda: False)

    data = instance_service.instance_data(1)
    by_target = {i["target"]: i for i in data["items"]}

    assert by_target["mods"]["size_bytes"] == 1000
    assert by_target["mods"]["paths"] == ["workshop"]
    assert by_target["saves"]["size_bytes"] == 100
    assert by_target["saves"]["paths"] == ["save"]
    assert by_target["logs"]["files"] == 1


def test_clear_instance_data_wipes_only_the_chosen_targets(tmp_path, monkeypatch):
    idir = _seed_instance_data(tmp_path, monkeypatch)
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "exited")

    # The real delete runs in a sibling container (the files are root-owned);
    # here, act it out on the paths the service asks to remove.
    asked = {}

    class FakeContainers:
        def run(self, image, entrypoint=None, command=None, **kw):
            asked["script"] = command[1]
            import shutil
            for part in command[1].split("rm -rf ")[1:]:
                rel = part.split("'")[1].replace("/idata/", "")
                shutil.rmtree(idir / rel, ignore_errors=True)

    monkeypatch.setattr(
        instance_service.docker_service, "get_client",
        lambda: type("C", (), {"containers": FakeContainers()})(),
    )
    monkeypatch.setattr(instance_service.docker_service, "host_path_for", lambda p: p)

    out = instance_service.clear_instance_data(1, ["mods", "saves"])

    assert {r["target"] for r in out["removed"]} == {"mods", "saves"}
    assert not (idir / "workshop" / "591AF5BDA9F7CEEB").exists()  # bake thrown away
    assert not (idir / "profile" / "save").exists()               # save wiped
    assert (idir / "profile" / "logs" / "session1" / "console.log").exists()  # kept
    assert (idir / "configs" / "server.json").exists()            # never touched
    # the dirs the server expects are recreated, empty
    assert (idir / "workshop").is_dir() and not any((idir / "workshop").iterdir())


def test_clear_instance_data_refuses_while_the_server_runs(tmp_path, monkeypatch):
    _seed_instance_data(tmp_path, monkeypatch)
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "running")
    with pytest.raises(instance_service.InstanceError, match="Stop the server"):
        instance_service.clear_instance_data(1, ["saves"])


def test_clear_instance_data_rejects_an_unknown_target(tmp_path, monkeypatch):
    _seed_instance_data(tmp_path, monkeypatch)
    monkeypatch.setattr(instance_service, "container_status", lambda _id: "exited")
    # Targets are a fixed vocabulary; nothing a caller sends reaches a path or a shell.
    with pytest.raises(instance_service.InstanceError, match="Nothing selected"):
        instance_service.clear_instance_data(1, ["../../etc"])


def test_instance_data_404s_for_an_instance_that_does_not_exist(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    # Without this it happily reported "0 B, nothing here" for any id at all.
    with pytest.raises(instance_service.InstanceError, match="not found"):
        instance_service.instance_data(999)


def test_list_and_resolve_log_files(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    profile = tmp_path / "instances" / "1" / "profile" / "logs" / "session1"
    profile.mkdir(parents=True)
    (profile / "console.log").write_text("hello")
    (profile / "crash.mdmp").write_bytes(b"\x00\x01")
    (profile / "ignore.dat").write_text("nope")  # non-log suffix filtered out

    files = instance_service.list_log_files(1)
    paths = {f["path"] for f in files}
    assert "logs/session1/console.log" in paths
    assert "logs/session1/crash.mdmp" in paths
    assert not any("ignore.dat" in p for p in paths)

    resolved = instance_service.resolve_log_file(1, "logs/session1/console.log")
    assert resolved.read_text() == "hello"


def test_resolve_log_file_blocks_traversal(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    (tmp_path / "instances" / "1" / "profile").mkdir(parents=True)
    (tmp_path / "secret.log").write_text("secret")
    with pytest.raises(instance_service.InstanceError):
        instance_service.resolve_log_file(1, "../../../../secret.log")


def test_prune_old_logs_removes_stale_sessions(tmp_path, monkeypatch):
    import os
    import time

    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "log_retention_days", 7)
    logs = tmp_path / "instances" / "1" / "profile" / "logs"
    old = logs / "old_session"
    new = logs / "new_session"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "console.log").write_text("x")
    old_time = time.time() - 10 * 86400
    os.utime(old, (old_time, old_time))

    assert instance_service.prune_old_logs() == 1
    assert not old.exists()
    assert new.exists()


def test_server_files_ready_detects_binary(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "serverfiles_dir", str(tmp_path))
    assert instance_service.server_files_ready("stable") is False
    branch_dir = tmp_path / "stable"
    branch_dir.mkdir()
    (branch_dir / instance_service.SERVER_BINARY).write_text("#!/bin/sh\n")
    assert instance_service.server_files_ready("stable") is True
    # a different branch is still not ready
    assert instance_service.server_files_ready("experimental") is False


def test_render_no_rcon_when_template_has_none():
    spec = TemplateSpec(name="t", scenario_id="{ABC}Missions/x.conf")  # no rcon_password
    cfg = instance_service.render_instance_config(render_config_json(spec), _inst(), "")
    assert "rcon" not in cfg
    # a2s still gets the instance port
    assert cfg["a2s"]["port"] == 17780


def test_render_preserves_scenario_and_mods():
    cfg = instance_service.render_instance_config(_template_config(), _inst(), "")
    assert cfg["game"]["scenarioId"] == "{ABC}Missions/x.conf"
    assert cfg["game"]["mods"][0]["modId"] == "AAA"


def test_create_container_uses_acemod_contract(tmp_path, monkeypatch):
    """Guard the container spec: image, volumes, ports, env, labels.

    This is what would break a real launch, so verify it without a daemon.
    """
    import config
    from services import docker_service

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "serverfiles_dir", str(tmp_path / "sf"))
    monkeypatch.setattr(config.settings, "reforger_server_image", "test/image:1")
    monkeypatch.setattr(config.settings, "public_address", "203.0.113.5")
    # host_path_for is identity outside a container
    monkeypatch.setattr(docker_service, "host_path_for", lambda p: p)

    captured = {}

    class FakeContainers:
        def create(self, image, **kw):
            captured["image"] = image
            captured.update(kw)
            class C:
                id = "deadbeefcafe0000"
            return C()

    class FakeClient:
        containers = FakeContainers()

    monkeypatch.setattr(docker_service, "get_client", lambda: FakeClient())

    inst = _inst(branch="experimental")
    cfg_path = tmp_path / "instances" / "1" / "configs" / "server.json"
    instance_service._create_container(inst, cfg_path)

    assert captured["image"] == "test/image:1"
    assert captured["name"] == "reforger-instance-1"
    # ACE Mod contract: use our mounted config, don't self-generate
    assert captured["environment"]["ARMA_CONFIG"] == "server.json"
    # never self-install: instances run the pre-downloaded files
    assert captured["environment"]["SKIP_INSTALL"] == "true"
    assert captured["environment"]["STEAM_APPID"] == "1890870"  # experimental
    assert captured["environment"]["SERVER_PUBLIC_PORT"] == "2005"
    assert captured["environment"]["SERVER_PUBLIC_ADDRESS"] == "203.0.113.5"
    # server binds the same port it is published on (host == container)
    assert captured["environment"]["SERVER_BIND_PORT"] == "2005"
    # -logStats is injected so the server emits FPS/player status lines (#38)
    assert "-logStats" in captured["environment"]["ARMA_PARAMS"]
    # ports published 1:1 so A2S/RCON queries reach the right internal port
    assert captured["ports"]["2005/udp"] == 2005
    assert captured["ports"]["17780/udp"] == 17780
    assert captured["ports"]["20002/udp"] == 20002
    # volumes: shared serverfiles at /reforger + per-instance dirs
    binds = {v["bind"] for v in captured["volumes"].values()}
    assert binds == {"/reforger", "/reforger/Configs", "/home/profile", "/reforger/workshop"}
    # labels let us rediscover the container after a restart
    assert captured["labels"][docker_service.LABEL_INSTANCE_ID] == "1"
    assert captured["labels"][docker_service.LABEL_ROLE] == docker_service.ROLE_INSTANCE
    # auto_restart default True -> container survives Docker/host restart (#17)
    assert captured["restart_policy"] == {"Name": "unless-stopped"}


# --------------------------------------------------------------------------- #
# Scheduled restarts
# --------------------------------------------------------------------------- #

def test_normalise_times_sorts_dedupes_and_pads():
    assert instance_service._normalise_times(["4:00", "16:30", "04:00"]) == ["04:00", "16:30"]


@pytest.mark.parametrize("bad", ["24:00", "12:60", "noon", "1200", "", "12:5"])
def test_normalise_times_rejects_malformed(bad):
    with pytest.raises(instance_service.InstanceError):
        instance_service._normalise_times([bad])


def test_schedule_times_reads_and_survives_garbage():
    assert instance_service.schedule_times(_inst()) == []  # default: no schedule
    good = _inst(restart_schedule_json=json.dumps({"times": ["04:00", "16:00"]}))
    assert instance_service.schedule_times(good) == ["04:00", "16:00"]
    assert instance_service.schedule_times(_inst(restart_schedule_json="not json")) == []


def test_due_scheduled_restart_fires_once_per_window():
    times = ["04:00", "16:00"]
    grace = instance_service.SCHEDULE_CATCHUP_GRACE_SECONDS

    # Just before 04:00 — nothing due.
    assert instance_service._due_scheduled_restart(
        times, datetime(2026, 7, 8, 3, 59), None, grace
    ) is None

    # At 04:00 with no prior service — the 04:00 occurrence is due.
    now = datetime(2026, 7, 8, 4, 0, 10)
    due = instance_service._due_scheduled_restart(times, now, None, grace)
    assert due == datetime(2026, 7, 8, 4, 0)

    # Already serviced this window — not due again.
    assert instance_service._due_scheduled_restart(times, now, due, grace) is None


def test_due_scheduled_restart_skips_stale_window():
    # 04:00 schedule, but it is now 06:00 (2h late) — beyond the 1h grace, so
    # a manager that was down does not restart a recovered server on boot.
    assert instance_service._due_scheduled_restart(
        ["04:00"], datetime(2026, 7, 8, 6, 0), None,
        instance_service.SCHEDULE_CATCHUP_GRACE_SECONDS,
    ) is None


def test_next_scheduled_restart_today_then_tomorrow():
    times = ["04:00", "16:00"]
    # before both -> next is today 04:00
    assert instance_service._next_scheduled_restart(
        times, datetime(2026, 7, 8, 2, 0)
    ) == datetime(2026, 7, 8, 4, 0)
    # between them -> next is today 16:00
    assert instance_service._next_scheduled_restart(
        times, datetime(2026, 7, 8, 10, 0)
    ) == datetime(2026, 7, 8, 16, 0)
    # after both -> wraps to tomorrow's earliest (04:00)
    assert instance_service._next_scheduled_restart(
        times, datetime(2026, 7, 8, 18, 0)
    ) == datetime(2026, 7, 9, 4, 0)
    # no schedule -> None
    assert instance_service._next_scheduled_restart([], datetime(2026, 7, 8, 18, 0)) is None


def test_next_restart_label_formats_server_local():
    inst = _inst(restart_schedule_json=json.dumps({"times": ["16:00"]}))
    label = instance_service.next_restart_label(inst, now=datetime(2026, 7, 8, 10, 0))
    assert label == "2026-07-08 16:00"
    assert instance_service.next_restart_label(_inst()) is None  # no schedule


def test_due_scheduled_restart_picks_latest_of_several():
    # Both 04:00 and 05:00 are past and within grace at 05:10 — pick the latest.
    due = instance_service._due_scheduled_restart(
        ["04:00", "05:00"], datetime(2026, 7, 8, 5, 10), None, 3600
    )
    assert due == datetime(2026, 7, 8, 5, 0)


def test_container_uptime_seconds_parses_docker_timestamp():
    from datetime import datetime, timedelta

    started = (datetime.now(UTC) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%S.123456789Z"
    )
    c = type("C", (), {"attrs": {"State": {"StartedAt": started}}})()
    secs = instance_service._container_uptime_seconds(c)
    assert secs is not None and 290 <= secs <= 310  # ~5 minutes


def test_container_uptime_none_when_never_started():
    c = type("C", (), {"attrs": {"State": {"StartedAt": "0001-01-01T00:00:00Z"}}})()
    assert instance_service._container_uptime_seconds(c) is None


def test_container_ports_match_detects_stale_mapping():
    inst = _inst()  # game 2005, a2s 17780, rcon 20002

    class FakeContainer:
        def __init__(self, bindings):
            self.attrs = {"HostConfig": {"PortBindings": bindings}}

        def reload(self):
            pass

    current = FakeContainer({
        "2005/udp": [{"HostIp": "", "HostPort": "2005"}],
        "17780/udp": [{"HostIp": "", "HostPort": "17780"}],
        "20002/udp": [{"HostIp": "", "HostPort": "20002"}],
    })
    assert instance_service._container_ports_match(current, inst) is True

    # Pre-v0.16 mapping: A2S/RCON forwarded from fixed internal 17777/19999
    stale = FakeContainer({
        "2001/udp": [{"HostIp": "", "HostPort": "2005"}],
        "17777/udp": [{"HostIp": "", "HostPort": "17780"}],
        "19999/udp": [{"HostIp": "", "HostPort": "20002"}],
    })
    assert instance_service._container_ports_match(stale, inst) is False


def test_container_ports_match_true_when_unreadable():
    # A container we can't inspect must never be force-recreated.
    class Broken:
        def reload(self):
            raise instance_service.DockerException("boom")
        attrs = {}

    assert instance_service._container_ports_match(Broken(), _inst()) is True


def test_create_container_applies_launch_params(tmp_path, monkeypatch):
    import config
    from services import docker_service
    from services.template_service import LaunchParams

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "serverfiles_dir", str(tmp_path / "sf"))
    monkeypatch.setattr(docker_service, "host_path_for", lambda p: p)

    captured = {}

    class FakeContainers:
        def create(self, image, **kw):
            captured.update(kw)
            return type("C", (), {"id": "x" * 16})()

    monkeypatch.setattr(docker_service, "get_client",
                        lambda: type("Cl", (), {"containers": FakeContainers()})())

    launch = LaunchParams(max_fps=90, no_backend=True, auto_reload_scenario=300)
    instance_service._create_container(_inst(), tmp_path / "server.json", launch)
    env = captured["environment"]
    assert env["ARMA_MAX_FPS"] == "90"
    assert "-noBackend" in env["ARMA_PARAMS"]
    assert "-autoreload 300" in env["ARMA_PARAMS"]


# --------------------------------------------------------------------------- #
# #113: network staleness detection + graceful manager shutdown/resume
# --------------------------------------------------------------------------- #

class _FakeNetContainer:
    def __init__(self, networks, fail=False):
        self.attrs = {"NetworkSettings": {"Networks": networks}}
        self._fail = fail

    def reload(self):
        if self._fail:
            raise AttributeError("cannot inspect")


def _patch_live_network(monkeypatch, net_id="live-id", missing=False):
    from docker.errors import NotFound

    from services import docker_service

    class FakeNetworks:
        def get(self, name):
            if missing:
                raise NotFound("no such network")
            return type("N", (), {"id": net_id})()

    monkeypatch.setattr(docker_service, "get_client",
                        lambda: type("Cl", (), {"networks": FakeNetworks()})())


def test_container_network_ok_detects_disconnect_and_stale_id(monkeypatch):
    # A container disconnected from the network (the manual workaround for the
    # compose-down failure) or pointing at a removed-and-recreated network's old
    # id starts without DNS: 'Curl error=Could not resolve hostname' (#113).
    _patch_live_network(monkeypatch, net_id="live-id")
    ok = instance_service._container_network_ok

    assert ok(_FakeNetContainer({"reforger-net": {"NetworkID": "live-id"}})) is True
    # empty NetworkID (stopped container): nothing to compare -> keep it
    assert ok(_FakeNetContainer({"reforger-net": {"NetworkID": ""}})) is True
    # endpoint gone entirely -> the docker-network-disconnect case
    assert ok(_FakeNetContainer({})) is False
    # endpoint referencing the OLD network id -> the down/up recreate case
    assert ok(_FakeNetContainer({"reforger-net": {"NetworkID": "old-id"}})) is False


def test_container_network_ok_never_destroys_what_it_cannot_verify(monkeypatch):
    _patch_live_network(monkeypatch, missing=True)
    # the network itself is unreadable/absent -> recreating the container
    # cannot help; keep it and let the start fail loudly
    assert instance_service._container_network_ok(
        _FakeNetContainer({"reforger-net": {"NetworkID": "x"}})) is True
    _patch_live_network(monkeypatch)
    assert instance_service._container_network_ok(
        _FakeNetContainer({}, fail=True)) is True


class _FakeLifecycleContainer:
    def __init__(self, status):
        self.status = status
        self.id = f"cid-{status}"
        self.name = f"c-{status}"
        self.stopped = False
        self.removed = False

    def stop(self, timeout=None):
        self.stopped = True

    def remove(self, force=False):
        self.removed = True


def test_shutdown_all_instances_stops_records_and_removes(tmp_path, monkeypatch):
    import config
    from services import docker_service

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(docker_service, "ping", lambda: True)
    running = _FakeLifecycleContainer("running")
    exited = _FakeLifecycleContainer("exited")
    monkeypatch.setattr(docker_service, "instance_containers",
                        lambda: {1: running, 2: exited})

    instance_service.shutdown_all_instances()

    # the running server was stopped gracefully; BOTH containers were removed
    # so no endpoint keeps the compose network 'in use' (#113)
    assert running.stopped and running.removed
    assert not exited.stopped and exited.removed
    # ...and only the running one is recorded for resume on next boot
    assert json.loads((tmp_path / "resume_instances.json").read_text()) == [1]


def test_resume_interrupted_instances_starts_recorded_and_clears(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    (tmp_path / "resume_instances.json").write_text("[1, 2]")
    started = []
    monkeypatch.setattr(instance_service, "start_instance",
                        lambda iid: started.append(iid))

    instance_service.resume_interrupted_instances()

    assert started == [1, 2]
    # the record is consumed: a crash loop must not replay it forever
    assert not (tmp_path / "resume_instances.json").exists()
    instance_service.resume_interrupted_instances()
    assert started == [1, 2]


def test_resume_interrupted_instances_tolerates_garbage(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "data_dir", str(tmp_path))
    (tmp_path / "resume_instances.json").write_text("not json")

    instance_service.resume_interrupted_instances()  # must not raise

    assert not (tmp_path / "resume_instances.json").exists()
