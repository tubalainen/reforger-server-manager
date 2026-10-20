"""The first-run setup screen is decided once, for brand-new installs only (#241)."""
from sqlmodel import Session

import models
from services import onboarding
from services.image_service import steamcmd_image
from services.steam_service import steam


def _add_template():
    with Session(models.get_engine()) as session:
        session.add(models.Template(name="Conflict", config_json="{}"))
        session.commit()


def test_brand_new_install_gets_the_setup_screen():
    onboarding.settle()
    assert onboarding.pending() is True


def test_install_with_a_template_does_not():
    _add_template()
    onboarding.settle()
    assert onboarding.pending() is False


def test_install_with_server_files_does_not(monkeypatch):
    monkeypatch.setattr(
        steam, "installed_info", lambda b: {"build_id": "1"} if b == "stable" else None
    )
    onboarding.settle()
    assert onboarding.pending() is False


def test_decided_once_so_it_survives_a_download_and_a_restart(monkeypatch):
    onboarding.settle()
    # Server files arrive halfway through the setup screen; the manager restarts.
    monkeypatch.setattr(steam, "installed_info", lambda b: {"build_id": "1"})
    onboarding.settle()
    assert onboarding.pending() is True


def test_finish_hides_it_for_good():
    onboarding.settle()
    onboarding.finish()
    onboarding.settle()
    assert onboarding.pending() is False


def test_never_decided_reads_as_not_pending():
    # settle() failed at startup: no setup screen rather than a stuck one.
    assert onboarding.pending() is False


def test_setup_api(logged_in):
    # The client's startup decided it on an empty database.
    assert logged_in.get("/api/system/setup").json() == {"pending": True}
    assert logged_in.post("/api/system/setup/done").json() == {"pending": False}
    assert logged_in.get("/api/system/setup").json() == {"pending": False}


def test_setup_api_requires_session(client):
    assert client.get("/api/system/setup").status_code == 401
    assert client.post("/api/system/setup/done").status_code == 401


def test_serverfiles_status_reports_the_steamcmd_image(logged_in):
    body = logged_in.get("/api/serverfiles").json()
    assert body["steamcmd_image_present"] is False  # Docker is down in tests
    assert body["steamcmd_image_job"] is None


def test_steamcmd_image_pull_needs_docker(logged_in):
    assert logged_in.post("/api/serverfiles/steamcmd-image/pull").status_code == 409


def test_steamcmd_image_service_reads_its_own_setting(monkeypatch):
    import config

    monkeypatch.setattr(config.settings, "steamcmd_image", "steamcmd/steamcmd:test")
    assert steamcmd_image.ref == "steamcmd/steamcmd:test"
