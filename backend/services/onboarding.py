"""The first-run setup screen (#241).

A brand-new install has nothing a server needs: no SteamCMD image, no server
runtime image and no game server files. The GUI opens on a setup screen that
fetches all three, and it does so only on a brand-new install.

"Brand new" is decided once, on the first start of a manager that knows about
this screen, and remembered (models.AppSetting): a database without a single
template or server, and no server files on disk. An install that already has
any of them was set up before this screen existed and never sees it. From then
on the screen stays until the user finishes or skips it, so reloading the page
halfway through a download does not lose it, and deleting every server later
does not bring it back.
"""
import json
import logging
from datetime import UTC, datetime

from sqlmodel import Session, select

import config
import models
from services.steam_service import steam

logger = logging.getLogger("manager.onboarding")

SETTINGS_KEY = "onboarding"


def _read() -> dict | None:
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, SETTINGS_KEY)
        raw = row.value if row else None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _write(done: bool) -> None:
    with Session(models.get_engine()) as session:
        row = session.get(models.AppSetting, SETTINGS_KEY)
        if row is None:
            row = models.AppSetting(key=SETTINGS_KEY)
        row.value = json.dumps({"done": done})
        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()


def brand_new() -> bool:
    """No template, no server and no server files: nothing was ever set up."""
    with Session(models.get_engine()) as session:
        if session.exec(select(models.Template.id).limit(1)).first() is not None:
            return False
        if session.exec(select(models.Instance.id).limit(1)).first() is not None:
            return False
    return not any(steam.installed_info(branch) for branch in config.BRANCHES)


def settle() -> None:
    """Decide, once, whether this install gets the setup screen (blocking)."""
    if _read() is not None:
        return
    fresh = brand_new()
    _write(done=not fresh)
    if fresh:
        logger.info("Brand-new install: the GUI opens on the setup screen")


def pending() -> bool:
    """The setup screen is still to be shown. Unknown reads as not pending."""
    state = _read()
    return state is not None and not state.get("done", True)


def finish() -> None:
    """The user finished or skipped the setup screen; never show it again."""
    _write(done=True)
    logger.info("First-run setup finished")
