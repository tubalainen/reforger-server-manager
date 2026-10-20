"""manage.py's server commands, which `rsm cli` runs inside a manager (#237)."""
from contextlib import nullcontext

import pytest
from sqlmodel import Session

import models
from services import instance_service


@pytest.fixture()
def manage(client, monkeypatch):
    """manage.py talking to the test app, with the session it mints itself."""
    import auth
    import manage as manage_mod

    monkeypatch.setattr(manage_mod.os, "geteuid", lambda: 1000, raising=False)

    def fake_client():
        client.cookies.clear()
        client.cookies.set(auth.cookie_name(), auth.session_token())
        return nullcontext(client)

    monkeypatch.setattr(manage_mod, "_client", fake_client)
    return manage_mod


@pytest.fixture()
def two_servers():
    with Session(models.get_engine()) as session:
        session.add(models.Template(id=1, name="Everon", scenario_name="Conflict",
                                    config_json='{"game": {"maxPlayers": 64}}'))
        session.add(models.Instance(id=1, name="Conflict\tEveron", template_id=1,
                                    game_port=2001, a2s_port=17771, rcon_port=19991))
        session.add(models.Instance(id=2, name="Training", template_id=1,
                                    game_port=2002, a2s_port=17772, rcon_port=19992))
        session.commit()


def test_servers_prints_one_line_per_server_with_its_own_session(manage, two_servers, capsys):
    assert manage.main(["servers"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    first = lines[0].split("\t")
    assert len(first) == len(manage.SERVER_COLUMNS)
    row = dict(zip(manage.SERVER_COLUMNS, first, strict=True))
    # Docker is off in the tests: no figures, and a tab in a name cannot split it.
    assert row["id"] == "1" and row["status"] == "unknown"
    assert row["max_players"] == "64" and row["players"] == "-"
    assert row["name"] == "Conflict Everon"


def test_a_stop_goes_through_the_manager(manage, two_servers, monkeypatch, capsys):
    stopped = []
    monkeypatch.setattr(instance_service, "stop_instance", stopped.append)
    assert manage.main(["stop", "2"]) == 0
    assert stopped == [2]
    assert capsys.readouterr().out.startswith("Training: ")


def test_a_refused_start_says_why(manage, two_servers, capsys):
    # Docker is off in the tests, so the manager refuses with a 409.
    assert manage.main(["start", "1"]) == 1
    assert "Docker daemon is not reachable" in capsys.readouterr().err


def test_bad_arguments_are_refused(manage, capsys):
    assert manage.main(["stop"]) == 2
    assert manage.main(["stop", "one"]) == 2
    assert manage.main(["servers", "extra"]) == 2
    assert "usage" in capsys.readouterr().err


def test_without_a_session_the_manager_says_no(client, monkeypatch, capsys):
    import manage as manage_mod

    monkeypatch.setattr(manage_mod.os, "geteuid", lambda: 1000, raising=False)
    client.cookies.clear()
    monkeypatch.setattr(manage_mod, "_client", lambda: nullcontext(client))
    assert manage_mod.main(["servers"]) == 1
    assert "401" in capsys.readouterr().err
