"""Guard: a release that changes the user's own setup files must say so (#167).

Users do not clone this repository. They download `docker-compose.yaml` and copy
`.env.example` to `.env` once, and from then on `docker compose pull` / `rsm
update` / the Windows start script only refresh the *image*. So when a release
changes `.env.example` or a compose file, nothing carries that change onto their
disk — they have to edit or re-download the file themselves, and the only place
that can tell them is the release notes.

We kept forgetting (v0.50.0 added AI_ORDER_URL/MODEL/KEY to `.env.example` and
the notes never said an existing `.env` would not have them). This check makes
forgetting fail the build instead:

  * work out which watched files changed since the last released tag;
  * if any did, `docs/release-notes/v<APP_VERSION>.md` must exist and carry the
    "## Updating your setup files" section;
  * every `.env` key added since that tag must be named somewhere in the notes.

It never inspects prose quality — it only enforces that the section exists and
that no new key goes unmentioned. Run it locally with:

    python scripts/ci/release_guard.py
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Files that live on the USER's disk and are never updated by pulling an image.
# Deliberately not everything under scripts/: the Windows scripts refresh
# themselves on every start (#135 follow-up) and the installers are fetched fresh
# by curl, so neither needs the user to act. `rsm` only arrives with an installer
# run, so it does.
WATCHED = (
    ".env.example",
    "docker-compose*.yaml",
    "scripts/linux/rsm.sh",
)

# The section the notes must carry when a watched file changed. Kept as one exact
# heading so it is greppable, linkable and impossible to satisfy by accident.
MARKER = "## Updating your setup files"

_ENV_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Pure core (unit-tested)
# --------------------------------------------------------------------------- #

def watched_changes(changed_files: list[str]) -> list[str]:
    """The changed paths that a user would have to apply by hand."""
    return sorted(
        path for path in changed_files
        if any(fnmatch.fnmatch(path, pattern) for pattern in WATCHED)
    )


def env_keys(text: str) -> set[str]:
    """The KEY names assigned in a .env-style file (comments ignored)."""
    return set(_ENV_KEY_RE.findall(text or ""))


def problems(
    version: str,
    changed: list[str],
    notes_path: str,
    notes: str | None,
    added_keys: set[str],
) -> list[str]:
    """What is wrong with this release's notes, in the order to fix it.

    `notes` is None when the file does not exist. An empty `changed` list means
    the user has nothing to apply, so nothing is required.
    """
    if not changed:
        return []
    if notes is None:
        return [
            f"{', '.join(changed)} changed, but there are no release notes at "
            f"{notes_path} for version {version}."
        ]

    found = []
    if MARKER.lower() not in notes.lower():
        found.append(
            f"{', '.join(changed)} changed, so {notes_path} must contain a "
            f'"{MARKER}" section telling users to update their own copy.'
        )
    unmentioned = sorted(key for key in added_keys if key not in notes)
    if unmentioned:
        found.append(
            f"{notes_path} never mentions the new .env key(s): "
            f"{', '.join(unmentioned)}."
        )
    return found


def guidance(changed: list[str], added_keys: set[str], removed_keys: set[str]) -> str:
    """A ready-to-paste section for whoever has to fix the failure."""
    lines = [f"{MARKER}", ""]
    if added_keys:
        lines += [
            "This release adds settings to `.env`. Your existing `.env` will not",
            "have them — it is never overwritten — so add them by hand:",
            "",
            "```bash",
            *[f"{key}=" for key in sorted(added_keys)],
            "```",
            "",
        ]
    if removed_keys:
        lines += [
            f"No longer used, safe to delete from `.env`: "
            f"{', '.join('`' + k + '`' for k in sorted(removed_keys))}.",
            "",
        ]
    if any(c.startswith("docker-compose") for c in changed):
        lines += [
            "The **compose file changed**. Pulling the image does not update it —",
            "re-download it (or re-run the installer) before restarting. See",
            "[Updating your setup files](https://github.com/tubalainen/reforger-server-manager#updating-your-setup-files).",
            "",
        ]
    if "scripts/linux/rsm.sh" in changed:
        lines += [
            "The `rsm` command changed; `rsm update` refreshes it for you from",
            "0.51.1 on, otherwise re-run the Linux installer once.",
            "",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Git / filesystem layer
# --------------------------------------------------------------------------- #

def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def app_version() -> str:
    text = (REPO_ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION = "([^"]+)"', text)
    if not match:
        sys.exit("Could not read APP_VERSION from backend/config.py")
    return match.group(1)


def latest_tag() -> str | None:
    """The highest v* tag, or None in a clone without tags (nothing to compare)."""
    tags = _git("tag", "--list", "v*", "--sort=-v:refname").splitlines()
    return tags[0] if tags else None


def file_at(ref: str, path: str) -> str:
    """A file's contents at a git ref; empty string when it did not exist."""
    try:
        return _git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        help="Compare against this ref instead of the highest v* tag.",
    )
    args = parser.parse_args()

    base = args.base or latest_tag()
    if not base:
        print("No previous release tag to compare against — nothing to check.")
        return 0

    version = app_version()
    notes_path = f"docs/release-notes/v{version}.md"
    # `git diff <base>` compares the base against the WORKING TREE, so running
    # this locally before committing reports what CI will see. In CI the tree is
    # clean, which makes it identical to <base>..HEAD.
    changed = watched_changes(_git("diff", "--name-only", base).splitlines())

    print(f"Release guard: v{version}, comparing against {base}.")
    if not changed:
        print("No setup files changed — users have nothing to apply. OK.")
        return 0
    print(f"Setup files changed: {', '.join(changed)}")

    before = env_keys(file_at(base, ".env.example"))
    after = env_keys((REPO_ROOT / ".env.example").read_text(encoding="utf-8"))
    added, removed = after - before, before - after
    if added:
        print(f"New .env keys: {', '.join(sorted(added))}")
    if removed:
        print(f"Removed .env keys: {', '.join(sorted(removed))}")

    notes_file = REPO_ROOT / notes_path
    notes = notes_file.read_text(encoding="utf-8") if notes_file.is_file() else None
    found = problems(version, changed, notes_path, notes, added)
    if not found:
        print("Release notes tell users what to update. OK.")
        return 0

    for problem in found:
        print(f"::error::{problem}")
    print()
    print(f"Add this to {notes_path} (and fill in the prose):")
    print()
    print(guidance(changed, added, removed))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
