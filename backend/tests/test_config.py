import dataclasses

import config
from config import _port_range, startup_security_issues, web_exposed


def test_port_range_valid():
    assert _port_range("2001-2020", (1, 2)) == (2001, 2020)


def test_port_range_single_port():
    assert _port_range("5000-5000", (1, 2)) == (5000, 5000)


def test_port_range_malformed_falls_back():
    for raw in (None, "", "abc", "10", "20-10", "-5-10", "0-10"):
        assert _port_range(raw, (2001, 2020)) == (2001, 2020)


# --------------------------------------------------------------------------- #
# Startup security gate (security review R2/R3)
# --------------------------------------------------------------------------- #

def _settings(**over):
    base = dict(
        admin_username="admin",
        admin_password="a-strong-password",
        auth_enabled=True,
        web_bind="127.0.0.1",
        auth_delegated_ack=False,
        session_secret="x",
        session_ttl_hours=168,
        session_cookie_secure=False,
        data_dir="/data",
        serverfiles_dir="/serverfiles",
        steamcmd_timeout_minutes=60,
        log_retention_days=14,
        static_dir="",
        docker_network="reforger-net",
        reforger_server_image="img",
        steamcmd_image="img",
        public_address="",
        game_port_range=(2001, 2020),
        a2s_port_range=(17777, 17796),
        rcon_port_range=(19999, 20018),
        session_secret_generated=False,
    )
    base.update(over)
    return config.Settings(**base)


def test_web_exposed_detects_bind():
    for loopback in ("", "127.0.0.1", "localhost", "::1", "[::1]", " 127.0.0.1 "):
        assert web_exposed(_settings(web_bind=loopback)) is False
    for exposed in ("0.0.0.0", "192.168.1.10", "10.0.0.5", "203.0.113.9"):
        assert web_exposed(_settings(web_bind=exposed)) is True


def test_loopback_is_frictionless_even_when_insecure():
    # Example password + no auth on localhost are only warnings, never fatal.
    fatal, warnings = startup_security_issues(
        _settings(admin_password=config.EXAMPLE_PASSWORD)
    )
    assert fatal == []
    assert any("example value" in w for w in warnings)

    fatal, _ = startup_security_issues(_settings(auth_enabled=False))
    assert fatal == []


def test_exposed_with_example_password_is_fatal():
    fatal, _ = startup_security_issues(
        _settings(web_bind="0.0.0.0", admin_password=config.EXAMPLE_PASSWORD)
    )
    assert fatal and any("Refusing to start" in f for f in fatal)


def test_exposed_with_empty_credentials_is_fatal():
    fatal, _ = startup_security_issues(_settings(web_bind="0.0.0.0", admin_password=""))
    assert fatal and any("empty" in f for f in fatal)


def test_exposed_auth_disabled_without_ack_is_fatal():
    fatal, _ = startup_security_issues(_settings(web_bind="0.0.0.0", auth_enabled=False))
    assert fatal and any("AUTH_DELEGATED_ACK" in f for f in fatal)


def test_exposed_auth_disabled_with_ack_starts():
    # The documented opt-out: a proxy owns auth, operator acknowledged it.
    fatal, warnings = startup_security_issues(
        _settings(web_bind="0.0.0.0", auth_enabled=False, auth_delegated_ack=True)
    )
    assert fatal == []
    assert any("built-in login is DISABLED" in w for w in warnings)


def test_exposed_with_strong_password_starts():
    fatal, warnings = startup_security_issues(
        _settings(web_bind="0.0.0.0", admin_password="a-long-strong-password")
    )
    assert fatal == []


def test_settings_matches_gate_signature():
    # Guard against the helper drifting from the real dataclass fields.
    real = {f.name for f in dataclasses.fields(config.Settings)}
    assert real == set(_settings().__dict__)
