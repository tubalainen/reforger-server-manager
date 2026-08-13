"""Unit tests for the release guard (#167). Run: python -m pytest scripts/ci -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_guard import MARKER, env_keys, guidance, problems, watched_changes

NOTES_PATH = "docs/release-notes/v0.52.0.md"


def _check(changed, notes, added=frozenset()):
    return problems("0.52.0", changed, NOTES_PATH, notes, set(added))


def test_only_user_owned_files_are_watched():
    changed = watched_changes([
        ".env.example",
        "docker-compose.yaml",
        "docker-compose.vps.yaml",
        "docker-compose.windows.yaml",
        "scripts/linux/rsm.sh",
        # Not watched: these reach the user without them lifting a finger.
        "backend/main.py",
        "Dockerfile",
        "scripts/windows/start.ps1",      # self-updates on every launch
        "scripts/linux/install-vps.sh",   # curl'd fresh at install time
        "docs/release-notes/v0.52.0.md",
    ])
    assert changed == [
        ".env.example",
        "docker-compose.vps.yaml",
        "docker-compose.windows.yaml",
        "docker-compose.yaml",
        "scripts/linux/rsm.sh",
    ]


def test_a_release_that_touches_nothing_users_own_needs_nothing():
    assert _check([], None) == []
    assert _check([], "no section here") == []


def test_missing_notes_file_is_reported():
    found = _check([".env.example"], None)
    assert len(found) == 1
    assert NOTES_PATH in found[0]


def test_notes_without_the_section_fail():
    found = _check(["docker-compose.yaml"], "## What's new\n\nLots.")
    assert len(found) == 1
    assert MARKER in found[0]
    assert "docker-compose.yaml" in found[0]


def test_notes_with_the_section_pass():
    notes = f"## What's new\n\nLots.\n\n{MARKER}\n\nRe-download the compose file.\n"
    assert _check(["docker-compose.yaml"], notes) == []


def test_the_section_heading_is_matched_case_insensitively():
    notes = "## updating your setup files\n\nDo this.\n"
    assert _check(["docker-compose.yaml"], notes) == []


def test_a_new_env_key_must_be_named_in_the_notes():
    # The v0.50.0 miss this guard exists for: keys added, notes silent.
    notes = f"{MARKER}\n\nAdd AI_ORDER_URL to your .env.\n"
    found = _check([".env.example"], notes, {"AI_ORDER_URL", "AI_ORDER_KEY"})
    assert len(found) == 1
    assert "AI_ORDER_KEY" in found[0]
    assert "AI_ORDER_URL" not in found[0]  # that one was mentioned


def test_all_new_keys_named_passes():
    notes = f"{MARKER}\n\nAdd AI_ORDER_URL and AI_ORDER_KEY to your .env.\n"
    assert _check([".env.example"], notes, {"AI_ORDER_URL", "AI_ORDER_KEY"}) == []


def test_env_keys_reads_assignments_and_ignores_comments():
    text = (
        "# --- Misc ---\n"
        "# COMMENTED_OUT=1\n"
        "TZ=Europe/Stockholm\n"
        "AI_ORDER_URL=\n"
        "WEB_PORT=7780  # trailing comment\n"
        "not a key line\n"
        "  INDENTED=1\n"  # not a real assignment line
    )
    assert env_keys(text) == {"TZ", "AI_ORDER_URL", "WEB_PORT"}


def test_env_keys_of_empty_input():
    assert env_keys("") == set()
    assert env_keys(None) == set()


def test_guidance_names_the_new_keys_and_the_compose_file():
    text = guidance(["docker-compose.yaml", ".env.example"], {"NEW_KEY"}, {"OLD_KEY"})
    assert MARKER in text
    assert "NEW_KEY=" in text
    assert "OLD_KEY" in text
    assert "compose file changed" in text


def test_guidance_stays_quiet_about_things_that_did_not_change():
    text = guidance([".env.example"], {"NEW_KEY"}, set())
    assert "compose file changed" not in text
    assert "rsm" not in text
