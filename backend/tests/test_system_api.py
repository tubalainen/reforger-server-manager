import pytest

import system_api
from services import docker_service


def test_network_requires_login(client):
    assert client.get("/api/system/network").status_code == 401


def test_network_reports_configured_ranges(logged_in, monkeypatch):
    monkeypatch.setattr(docker_service, "is_docker_desktop", lambda: False)
    r = logged_in.get("/api/system/network")
    assert r.status_code == 200
    body = r.json()
    assert body["game_port_range"] == "2001-2020"
    assert body["a2s_port_range"] == "17777-17796"
    assert body["host"] == "linux"


def test_network_detects_docker_desktop(logged_in, monkeypatch):
    monkeypatch.setattr(docker_service, "is_docker_desktop", lambda: True)
    assert logged_in.get("/api/system/network").json()["host"] == "windows"


def test_firewall_commands_carry_only_player_facing_ports():
    game, a2s = (2001, 2020), (17777, 17796)
    win = system_api.windows_firewall_command(game, a2s)
    linux = system_api.linux_firewall_command(game, a2s)

    assert "-Protocol UDP -LocalPort 2001-2020,17777-17796" in win
    assert "sudo ufw allow 2001:2020/udp" in linux
    assert "sudo ufw allow 17777:17796/udp" in linux
    # RCON must never be opened for us by the command we hand the user.
    assert "19999" not in win and "19999" not in linux


@pytest.mark.parametrize(
    "operating_system,expected",
    [
        ("Docker Desktop", True),
        ("Ubuntu 24.04.1 LTS", False),
        ("", False),
    ],
)
def test_is_docker_desktop(monkeypatch, operating_system, expected):
    monkeypatch.setattr(
        docker_service, "daemon_info", lambda: {"OperatingSystem": operating_system}
    )
    assert docker_service.is_docker_desktop() is expected
