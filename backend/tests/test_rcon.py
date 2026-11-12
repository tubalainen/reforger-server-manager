"""The BattlEye RCon client and the kick/ban layer on top of it."""
import json
import socket
import threading
import uuid
from pathlib import Path

import pytest
from sqlmodel import Session

import config
from models import Instance, get_engine
from services import rcon, rcon_admin
from services.instance_service import InstanceError
from services.rcon import RconError, packet, parse

# --------------------------------------------------------------------------- #
# The wire format
# --------------------------------------------------------------------------- #


def test_packet_round_trip():
    body = bytes([rcon.COMMAND, 7]) + b"#players"
    assert parse(packet(body)) == body


def test_a_bad_checksum_or_header_is_not_a_packet():
    good = packet(b"\x01\x00hi")
    assert parse(b"XX" + good[2:]) is None
    assert parse(good[:2] + b"\x00\x00\x00\x00" + good[6:]) is None
    assert parse(b"BE") is None


# --------------------------------------------------------------------------- #
# Against a fake server on localhost
# --------------------------------------------------------------------------- #


class FakeServer:
    """A BattlEye RCon server in a thread: answers logins and commands."""

    def __init__(self, password="secret", replies=None, drop_first=0, split=None,
                 push_message=False, as_messages=False):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.password = password
        self.replies = replies or {}
        self.drop_first = drop_first      # ignore this many packets (UDP loss)
        self.split = split                # send replies in this many parts
        self.push_message = push_message  # send a 0x02 message before replying
        self.as_messages = as_messages    # Reforger: empty reply, output as messages
        self._msg_seq = 0
        self.commands: list[str] = []
        self.acks: list[int] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(2)
        self.sock.close()

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except (TimeoutError, OSError):
                continue
            if self.drop_first > 0:
                self.drop_first -= 1
                continue
            body = parse(data)
            if not body:
                continue
            kind = body[0]
            if kind == rcon.LOGIN:
                ok = body[1:].decode() == self.password
                self.sock.sendto(packet(bytes([rcon.LOGIN, 1 if ok else 0])), addr)
            elif kind == rcon.MESSAGE:
                self.acks.append(body[1])
            elif kind == rcon.COMMAND:
                seq, text = body[1], body[2:].decode()
                self.commands.append(text)
                if self.push_message:
                    self.sock.sendto(packet(bytes([rcon.MESSAGE, 42]) + b"chat"), addr)
                reply = self.replies.get(text, "").encode()
                if self.as_messages:
                    self.sock.sendto(packet(bytes([rcon.COMMAND, seq])), addr)
                    for line in [f"Processing Command: {text}", *reply.decode().split("\n")]:
                        if line:
                            self.sock.sendto(packet(bytes([rcon.MESSAGE, self._msg_seq])
                                                    + line.encode()), addr)
                            self._msg_seq = (self._msg_seq + 1) % 256
                elif self.split:
                    size = max(1, -(-len(reply) // self.split))
                    chunks = [reply[i:i + size] for i in range(0, len(reply), size)]
                    for index, chunk in reversed(list(enumerate(chunks))):
                        head = bytes([rcon.COMMAND, seq, 0, len(chunks), index])
                        self.sock.sendto(packet(head + chunk), addr)
                else:
                    self.sock.sendto(packet(bytes([rcon.COMMAND, seq]) + reply), addr)


def _run(server, commands, password="secret", **kw):
    return rcon.run("127.0.0.1", server.port, password, commands,
                    timeout=kw.pop("timeout", 0.5), **kw)


def test_login_and_a_command():
    with FakeServer(replies={"#players": "Players on server:\n0 Reidond"}) as server:
        assert _run(server, ["#players"]) == ["Players on server:\n0 Reidond"]
        assert server.commands[0] == "#players"


def test_the_session_logs_out_on_the_way_out():
    with FakeServer() as server:
        _run(server, ["#players"])
        for _ in range(20):
            if rcon.LOGOUT_COMMAND in server.commands:
                break
            threading.Event().wait(0.05)
        assert server.commands[-1] == rcon.LOGOUT_COMMAND


def test_a_wrong_password_is_said_as_such():
    with FakeServer() as server, pytest.raises(RconError, match="refused the RCON password"):
        _run(server, ["#players"], password="nope")


def test_lost_packets_are_sent_again():
    with FakeServer(replies={"#players": "ok"}, drop_first=1) as server:
        assert _run(server, ["#players"]) == ["ok"]


def test_a_reply_in_several_parts_is_put_back_together():
    text = "BanID ; Player UID ; Duration\n" + "\n".join(f"{n} ; uid{n} ; 0" for n in range(30))
    with FakeServer(replies={"#ban list 1": text}, split=3) as server:
        assert _run(server, ["#ban list 1"]) == [text]


def test_a_message_from_the_server_is_acknowledged_and_skipped():
    with FakeServer(replies={"#players": "ok"}, push_message=True) as server:
        assert _run(server, ["#players"]) == ["ok"]
        for _ in range(20):
            if server.acks:
                break
            threading.Event().wait(0.05)
        assert server.acks == [42]


def test_no_server_at_all_is_a_clear_error():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens there now
    with pytest.raises(RconError):
        rcon.run("127.0.0.1", port, "secret", ["#players"], timeout=0.2, attempts=2)


# --------------------------------------------------------------------------- #
# Kick and ban
# --------------------------------------------------------------------------- #


@pytest.fixture
def server_with_rcon(monkeypatch):
    """A running server whose last config.json has RCON on; returns (id, sent).

    Writes under the data folder the test run already uses: moving it would also
    lose the logged-in session the API test relies on.
    """
    from services import docker_service, instance_service

    with Session(get_engine()) as session:
        inst = Instance(name=f"rcon-{uuid.uuid4().hex[:8]}", template_id=1, branch="stable",
                        game_port=2001, a2s_port=17777, rcon_port=19999)
        session.add(inst)
        session.commit()
        session.refresh(inst)
        iid = inst.id
    configs = Path(config.settings.data_dir) / "instances" / str(iid) / "configs"
    configs.mkdir(parents=True, exist_ok=True)

    def write(rcon_block):
        cfg = {"game": {}, **({"rcon": rcon_block} if rcon_block else {})}
        (configs / instance_service.CONFIG_FILENAME).write_text(json.dumps(cfg))

    write({"password": "secret", "permission": "admin", "port": 19999})
    sent = []
    monkeypatch.setattr(instance_service, "container_status", lambda _i: "running")
    monkeypatch.setattr(docker_service, "use_host_network", lambda: True)
    monkeypatch.setattr(rcon_admin, "_docker_host", lambda: "198.51.100.1")
    monkeypatch.setattr(
        rcon, "run",
        lambda host, port, password, commands, **k: sent.append(
            (host, port, password, commands)) or ["" for _ in commands],
    )
    return iid, sent, write


def test_kick_sends_the_games_command(server_with_rcon):
    iid, sent, _ = server_with_rcon
    rcon_admin.kick(iid, 5)
    assert sent == [("198.51.100.1", 19999, "secret", ["#kick 5"])]


def test_ban_by_identity_for_a_day_with_a_reason(server_with_rcon):
    iid, sent, _ = server_with_rcon
    rcon_admin.ban(iid, identity_id="25C9BB9D-FDD7-4BE9-898E-CAC20A551DD2",
                   duration_seconds=86400, reason="  team\nkilling ")
    assert sent[0][3] == ["#ban create 25c9bb9d-fdd7-4be9-898e-cac20a551dd2 86400 team killing"]


def test_ban_by_player_id_for_ever(server_with_rcon):
    iid, sent, _ = server_with_rcon
    rcon_admin.ban(iid, player_id=3)
    assert sent[0][3] == ["#ban create 3 0"]


def test_a_reason_cannot_carry_a_second_command(server_with_rcon):
    iid, sent, _ = server_with_rcon
    rcon_admin.ban(iid, player_id=3, reason="x\n#shutdown\x00")
    assert sent[0][3] == ["#ban create 3 0 x #shutdown"]
    assert len(sent[0][3]) == 1


@pytest.mark.parametrize("kwargs", [
    {},                                      # no target
    {"identity_id": "not-an-identity"},
    {"player_id": 3, "duration_seconds": -1},
])
def test_ban_refuses_what_it_cannot_send(server_with_rcon, kwargs):
    iid, sent, _ = server_with_rcon
    with pytest.raises(InstanceError):
        rcon_admin.ban(iid, **kwargs)
    assert sent == []


def test_ban_list_and_remove(server_with_rcon):
    iid, sent, _ = server_with_rcon
    rcon_admin.ban_list(iid, 2)
    rcon_admin.ban_remove(iid, "12")
    with pytest.raises(InstanceError):
        rcon_admin.ban_remove(iid, "12; #shutdown")
    assert [c for *_, c in sent] == [["#ban list 2"], ["#ban remove 12"]]


def test_rcon_off_says_how_to_turn_it_on(server_with_rcon):
    iid, sent, write = server_with_rcon
    write(None)
    assert rcon_admin.status(iid) == {"enabled": False, "permission": None}
    with pytest.raises(InstanceError, match="RCON password"):
        rcon_admin.kick(iid, 1)
    assert sent == []


def test_monitor_permission_may_read_but_not_kick(server_with_rcon):
    iid, sent, write = server_with_rcon
    write({"password": "secret", "permission": "monitor"})
    assert rcon_admin.status(iid) == {"enabled": True, "permission": "monitor"}
    with pytest.raises(InstanceError, match="monitor"):
        rcon_admin.kick(iid, 1)
    rcon_admin.ban_list(iid)
    assert [c for *_, c in sent] == [["#ban list 1"]]


def test_a_stopped_server_is_not_asked(server_with_rcon, monkeypatch):
    from services import instance_service

    iid, sent, _ = server_with_rcon
    monkeypatch.setattr(instance_service, "container_status", lambda _i: "exited")
    with pytest.raises(InstanceError, match="not running"):
        rcon_admin.kick(iid, 1)


def test_no_answer_becomes_a_message_with_the_address(server_with_rcon, monkeypatch):
    iid, _sent, _ = server_with_rcon

    def silent(*a, **k):
        raise RconError("No answer from RCON on 198.51.100.1:19999")

    monkeypatch.setattr(rcon, "run", silent)
    with pytest.raises(InstanceError, match="firewall on the host"):
        rcon_admin.kick(iid, 1)


def test_bridge_networking_reaches_the_container_by_name(server_with_rcon, monkeypatch):
    from services import docker_service

    iid, sent, _ = server_with_rcon
    monkeypatch.setattr(docker_service, "use_host_network", lambda: False)
    rcon_admin.kick(iid, 1)
    assert sent[0][0] == docker_service.container_name(f"instance-{iid}")


def test_the_api_answers_with_the_reply_and_refusals_as_409(logged_in, server_with_rcon):
    iid, sent, write = server_with_rcon
    r = logged_in.post(f"/api/instances/{iid}/players/kick", json={"player_id": 4})
    assert r.status_code == 200 and r.json() == {"reply": ""}
    assert logged_in.get(f"/api/instances/{iid}/rcon").json()["enabled"] is True
    write(None)
    r = logged_in.post(f"/api/instances/{iid}/players/ban", json={"player_id": 4})
    assert r.status_code == 409


def test_the_docker_host_is_the_managers_own_gateway(monkeypatch):
    # host.docker.internal maps to docker0, which Docker does not let another
    # bridge network reach; the container's own gateway does answer.
    monkeypatch.setattr(rcon_admin, "_default_gateway", lambda: "172.21.0.1")
    assert rcon_admin._docker_host() == "172.21.0.1"
    monkeypatch.setattr(rcon_admin, "_default_gateway", lambda: None)
    assert rcon_admin._docker_host() == "host.docker.internal"


def test_reforger_sends_a_commands_output_as_messages_after_an_empty_reply():
    # Seen on Arma Reforger 1.9: COMMAND '' then MESSAGE 'Processing Command: ...'
    # then the output, one message per line.
    replies = {"#ban list": "Server has no bans to list.",
               "#players": "Players on server: [Player#] ; [Player UID] ; [Player Name]\n0 ; abc ; Reidond"}
    with FakeServer(replies=replies, as_messages=True) as server:
        out = _run(server, ["#ban list", "#players", "#kick 0"])
    assert out[0] == "Server has no bans to list."
    assert out[1].endswith("0 ; abc ; Reidond") and "Processing Command" not in out[1]
    assert out[2] == ""  # nothing printed: an empty answer, not an error


@pytest.mark.parametrize("reply", ["Player not found", "error 'ban create x 60'",
                                   "unknown command 'kik'"])
def test_a_command_the_server_refused_is_a_failure_not_a_success(server_with_rcon, monkeypatch,
                                                                  reply):
    iid, _sent, _ = server_with_rcon
    monkeypatch.setattr(rcon, "run", lambda *a, **k: [reply])
    with pytest.raises(InstanceError, match="could not"):
        rcon_admin.kick(iid, 99)


def test_an_ordinary_answer_is_passed_on(server_with_rcon, monkeypatch):
    iid, _sent, _ = server_with_rcon
    monkeypatch.setattr(rcon, "run", lambda *a, **k: ["Player #3 kicked"])
    assert rcon_admin.kick(iid, 3) == "Player #3 kicked"
    monkeypatch.setattr(rcon, "run", lambda *a, **k: [""])
    assert rcon_admin.kick(iid, 3) == ""
