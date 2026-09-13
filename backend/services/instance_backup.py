"""Backup and restore of a server instance's saved game data (#179).

A backup is one `.tar.gz` of everything under an instance's `profile/` that is
game state — the persistent world, and the databases the scenario and its mods
write next to it. What that means exactly is decided in one place,
``instance_service._state_paths``; this module only archives, lists and puts
back what that scan reports, so the row the GUI offers to clear and the files a
backup holds can never drift apart.

Two halves, two mechanisms, for one reason: the game server runs as root, so it
owns the files it writes.

* **Writing a backup** is a read, and those files are world-readable, so the
  manager builds the archive itself with ``tarfile``. That keeps backups working
  when the Docker daemon is not — the one moment you most want a copy of the
  save is when something is wrong.
* **Restoring** has to delete and replace root-owned trees, which uid 1000
  cannot do. That runs in a short-lived sibling container, exactly as
  ``clear_instance_data`` and ``steam_service.remove_files`` do.

Backups live under the instance's own directory (``data/instances/<id>/backups``)
— outside the two paths mounted into the server container, so the game can never
see or touch them, and purging an instance takes its backups with it.
"""
import io
import json
import logging
import os
import re
import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from docker.errors import DockerException
from sqlmodel import Session, select

import config
from models import Instance, Template, get_engine
from services import change_log, docker_service, instance_service, template_service
from services.instance_service import InstanceError

logger = logging.getLogger(__name__)

ARCHIVE_SUFFIX = ".tar.gz"
META_SUFFIX = ".json"

# The metadata is written twice: as a sidecar next to the archive (what the list
# is built from) and inside the archive itself, so a file downloaded today still
# says which template and scenario it came from when it is uploaded to a rebuilt
# host a year from now.
EMBEDDED_META = "rsm-backup.json"

# How many backups an instance keeps. The oldest are removed as new ones are
# made, so the shelf cannot grow without bound on a host nobody is watching.
KEEP = 10

# Ceilings for an uploaded archive. Neither is a limit anyone will meet with a
# real save; they are here so a hostile or broken file cannot fill the disk.
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_UPLOAD_MEMBERS = 200_000

# Backup ids are minted here (never taken from a request) and are the only thing
# that reaches a shell, inside single quotes. The pattern is still enforced on
# the way in, so a crafted id cannot walk out of the backups directory.
_ID_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")

SOURCE_MANUAL = "manual"
SOURCE_TEMPLATE_SWITCH = "template-switch"
SOURCE_PRE_RESTORE = "pre-restore"
SOURCE_UPLOAD = "upload"

# How a backup relates to what the instance is configured for right now (#181).
# "match" is the only one the engine will actually load.
FIT_MATCH = "match"
FIT_OTHER_HIVE = "other-hive"
FIT_OTHER_SCENARIO = "other-scenario"
FIT_UNKNOWN = "unknown"


def backups_dir(instance_id: int) -> Path:
    return Path(config.settings.data_dir) / "instances" / str(instance_id) / "backups"


def _profile_dir(instance_id: int) -> Path:
    return Path(config.settings.data_dir) / "instances" / str(instance_id) / "profile"


def _instance_dir(instance_id: int) -> Path:
    return Path(config.settings.data_dir) / "instances" / str(instance_id)


def _require_id(backup_id: str) -> str:
    if not _ID_RE.match(backup_id or ""):
        raise InstanceError("Unknown backup")
    return backup_id


def _mint_id(directory: Path) -> str:
    """A sortable timestamp id, made unique if one already exists in the second."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    candidate, n = stamp, 1
    while (directory / f"{candidate}{ARCHIVE_SUFFIX}").exists():
        n += 1
        candidate = f"{stamp}-{n}"
    return candidate


def _instance_context(instance_id: int) -> dict:
    """Who this backup belongs to, and what the template was configured for.

    Recorded on the archive so a restore can say "this save was made under
    scenario X, the server is now set to Y" instead of quietly loading a world
    the scenario cannot read.
    """
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        if not inst:
            raise InstanceError("Server not found")
        template = session.get(Template, inst.template_id)
        persistence = (
            template_service.persistence_summary(template.config_json)
            if template
            else {"persistence": False, "hive_id": None}
        )
        scenario_id = ""
        if template:
            try:
                scenario_id = (
                    (json.loads(template.config_json).get("game") or {})
                    .get("scenarioId", "")
                )
            except (ValueError, AttributeError):
                scenario_id = ""
        return {
            "instance_id": instance_id,
            "instance_name": inst.name,
            "template_id": template.id if template else None,
            "template_name": template.name if template else None,
            "scenario_id": scenario_id,
            "persistence": bool(persistence.get("persistence")),
            "hive_id": persistence.get("hive_id"),
        }


def _template_targets(session: Session) -> list[dict]:
    """Every template's save target: (scenario, hive id) — what a world needs to load."""
    out = []
    for template in session.exec(select(Template)).all():
        persistence = template_service.persistence_summary(template.config_json)
        try:
            scenario_id = (
                (json.loads(template.config_json).get("game") or {}).get("scenarioId", "")
            )
        except (ValueError, AttributeError):
            scenario_id = ""
        out.append({
            "id": template.id,
            "name": template.name,
            "scenario_id": scenario_id,
            "hive_id": persistence.get("hive_id"),
        })
    return out


def fit_of(meta: dict, current: dict) -> str:
    """How a backup relates to the instance's current setup (#181).

    A world is loaded by the scenario that wrote it, out of the hive its
    template names — so those two fields, not the template's identity, decide
    whether a restore will be visible in-game. A backup whose template was since
    renamed, edited or deleted still fits if the pair still matches.
    """
    written_for = (meta.get("scenario_id") or "").strip()
    if not written_for:
        return FIT_UNKNOWN  # an uploaded archive that never said
    if written_for != (current.get("scenario_id") or "").strip():
        return FIT_OTHER_SCENARIO
    if meta.get("hive_id") != current.get("hive_id"):
        return FIT_OTHER_HIVE
    return FIT_MATCH


def _misfit_reason(meta: dict, target: dict) -> str:
    """Why this backup would not load under that template, for the refusal (#184)."""
    name = target.get("name") or "that template"
    fit = fit_of(meta, target)
    if fit == FIT_UNKNOWN:
        return (
            "This archive does not record which scenario wrote it, so there is no "
            "way to tell whether this server would read it."
        )
    if fit == FIT_OTHER_SCENARIO:
        return (
            f"This world was written under scenario {meta.get('scenario_id')}, and "
            f"template \"{name}\" runs {target.get('scenario_id')}. A scenario does "
            "not read another scenario's world."
        )
    return (
        f"This world was written for hive id {meta.get('hive_id')}, and template "
        f"\"{name}\" saves to hive id {target.get('hive_id')}. The engine will not "
        "load one into the other."
    )


def _switch_candidate(meta: dict, templates: list[dict]) -> dict | None:
    """A template that would make this backup load, or None if none would.

    Preference order: the template recorded on the backup if it still writes to
    the same save; then any template matching both scenario and hive id; then
    one matching the scenario alone, flagged so the GUI can say the hive id
    still differs.
    """
    written_for = (meta.get("scenario_id") or "").strip()
    if not written_for:
        return None
    same_scenario = [t for t in templates if (t["scenario_id"] or "").strip() == written_for]
    if not same_scenario:
        return None
    exact = [t for t in same_scenario if t["hive_id"] == meta.get("hive_id")]
    recorded = [t for t in exact if t["id"] == meta.get("template_id")]
    chosen = (recorded or exact or same_scenario)[0]
    return {
        "id": chosen["id"],
        "name": chosen["name"],
        "hive_id": chosen["hive_id"],
        "exact": chosen in exact,
    }


def state_summary(instance_id: int) -> dict:
    """What a backup taken right now would hold: size, file count, top-level paths."""
    idir = _instance_dir(instance_id)
    paths = instance_service._state_paths(_profile_dir(instance_id))
    size, files = instance_service._dir_usage(paths)
    return {
        "size_bytes": size,
        "files": files,
        "paths": sorted(p.relative_to(idir).as_posix() for p in paths),
    }


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #

def _read_meta(meta_file: Path) -> dict | None:
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _display_time(iso: str) -> str:
    try:
        parsed = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    return change_log.format_local(
        parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    )


def list_backups(instance_id: int) -> list[dict]:
    """Every archive on this instance's shelf, newest first.

    The archive file is the record, not the sidecar: one that lost its metadata
    (hand-copied in, restored from elsewhere) is still listed and still
    restorable, just with less to say about itself.
    """
    directory = backups_dir(instance_id)
    if not directory.is_dir():
        return []
    out = []
    for archive in sorted(directory.glob(f"*{ARCHIVE_SUFFIX}")):
        backup_id = archive.name[: -len(ARCHIVE_SUFFIX)]
        if not _ID_RE.match(backup_id):
            continue
        meta = _read_meta(directory / f"{backup_id}{META_SUFFIX}") or {}
        try:
            stat = archive.stat()
        except OSError:
            continue
        created = meta.get("created_at") or datetime.fromtimestamp(
            stat.st_mtime, UTC
        ).isoformat()
        out.append({
            **meta,
            "id": backup_id,
            "archive_bytes": stat.st_size,
            "created_at": created,
            # Rendered here, in the manager's timezone, exactly as the change log
            # does (#112) — the browser's own zone is not the one the operator
            # reasons about when they compare this to a server log.
            "created_display": _display_time(created),
        })
    out.sort(key=lambda b: b["id"], reverse=True)
    return out


def _sweep_stray_sidecars(instance_id: int) -> None:
    """Drop metadata files whose archive is gone (#187).

    The archive is always written before its sidecar and both go together on
    delete, so a lone sidecar means something was interrupted or moved by hand.
    It is invisible to the listing, which is driven by archives, and would sit
    there for ever otherwise — so opening the card tidies up after it.
    """
    directory = backups_dir(instance_id)
    if not directory.is_dir():
        return
    for meta_file in directory.glob(f"*{META_SUFFIX}"):
        archive = directory / f"{meta_file.name[: -len(META_SUFFIX)]}{ARCHIVE_SUFFIX}"
        if archive.exists():
            continue
        try:
            meta_file.unlink()
            logger.info("Removed stray backup metadata %s", meta_file.name)
        except OSError:
            continue


def overview(instance_id: int) -> dict:
    """Everything the backups card needs in one call."""
    with Session(get_engine()) as session:
        if not session.get(Instance, instance_id):
            raise InstanceError("Server not found")
    _sweep_stray_sidecars(instance_id)
    running = (
        instance_service.container_status(instance_id) == "running"
        if docker_service.ping()
        else False
    )
    current = _instance_context(instance_id)
    with Session(get_engine()) as session:
        templates = _template_targets(session)
    backups = []
    for backup in list_backups(instance_id):
        fit = fit_of(backup, current)
        backups.append({
            **backup,
            "fit": fit,
            # What to switch this instance to so the world is actually read. Null
            # when it already fits, or when no template here targets that save.
            "switch_to": None if fit == FIT_MATCH else _switch_candidate(backup, templates),
        })
    return {
        "running": running,
        "keep": KEEP,
        # Every template's save target, so the restore dialog can offer them all
        # and annotate each with how it fits the chosen backup. The annotation is
        # a hint; the refusal in restore_backup is the authority (#184).
        "templates": templates,
        # What the instance is configured for *now*, so the GUI can say when a
        # backup was written under a different scenario — restoring one of those
        # gives the new scenario a world it cannot read (#179).
        "current": current,
        "state": state_summary(instance_id),
        "backups": backups,
    }


# --------------------------------------------------------------------------- #
# Creating
# --------------------------------------------------------------------------- #

def _add_file(tar: tarfile.TarFile, path: Path, arcname: str, skipped: list[str]) -> int:
    """Add one file; an unreadable one is recorded rather than aborting the backup.

    A backup that stops at the first permission problem is a backup you do not
    get. One that quietly leaves files out is worse. So: take everything that can
    be read, and hand the list of what could not back to the caller, which says
    so in the GUI and stores it on the archive.
    """
    try:
        tar.add(path, arcname=arcname, recursive=False)
        return path.stat().st_size if path.is_file() else 0
    except OSError as exc:
        logger.warning("Backup could not read %s: %s", path, exc)
        skipped.append(arcname)
        return 0


def _write_archive(archive: Path, idir: Path, paths: list[Path], base: dict) -> dict:
    """Build the .tar.gz and return the metadata describing what went into it.

    The metadata is written as the archive's last member as well as beside it, so
    a file downloaded today still says which template and scenario it came from
    when it is uploaded to a rebuilt host a year from now. It can only be written
    once the counts are known, which is why it is added here rather than by a
    second pass over a finished archive.
    """
    files = 0
    size = 0
    skipped: list[str] = []
    with tarfile.open(archive, "w:gz") as tar:
        for node in paths:
            arc = node.relative_to(idir).as_posix()
            if node.is_file():
                size += _add_file(tar, node, arc, skipped)
                files += 1
                continue
            _add_file(tar, node, arc, skipped)  # the directory entry itself
            walk = os.walk(node, onerror=lambda e: skipped.append(str(e)))
            for root, dirnames, filenames in walk:
                root_path = Path(root)
                dirnames.sort()
                for name in dirnames:
                    child = root_path / name
                    _add_file(tar, child, child.relative_to(idir).as_posix(), skipped)
                for name in sorted(filenames):
                    child = root_path / name
                    size += _add_file(tar, child, child.relative_to(idir).as_posix(), skipped)
                    files += 1
        meta = {**base, "files": files, "size_bytes": size, "skipped": skipped}
        payload = json.dumps(meta, indent=2).encode("utf-8")
        info = tarfile.TarInfo(EMBEDDED_META)
        info.size = len(payload)
        info.mtime = int(datetime.now(UTC).timestamp())
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
    return meta


def create_backup(
    instance_id: int,
    label: str = "",
    source: str = SOURCE_MANUAL,
    protect: str | None = None,
) -> dict:
    """Archive this instance's saved game data. Allowed while the server runs."""
    idir = _instance_dir(instance_id)
    context = _instance_context(instance_id)
    paths = instance_service._state_paths(_profile_dir(instance_id))
    if not paths:
        raise InstanceError(
            "There is no saved game data to back up yet — this server has not "
            "written a save or any scenario data."
        )
    running = (
        instance_service.container_status(instance_id) == "running"
        if docker_service.ping()
        else False
    )

    directory = backups_dir(instance_id)
    directory.mkdir(parents=True, exist_ok=True)
    backup_id = _mint_id(directory)
    archive = directory / f"{backup_id}{ARCHIVE_SUFFIX}"
    base = {
        **context,
        "id": backup_id,
        "created_at": datetime.now(UTC).isoformat(),
        "label": (label or "").strip()[:120],
        "source": source,
        "app_version": config.APP_VERSION,
        # A snapshot of a live server can catch a save point mid-write. It is
        # still worth having — it is just worth saying so, on the row and here.
        "server_running": running,
        "paths": sorted(p.relative_to(idir).as_posix() for p in paths),
    }
    try:
        meta = _write_archive(archive, idir, paths, base)
    except (OSError, tarfile.TarError) as exc:
        archive.unlink(missing_ok=True)
        logger.warning("Backup of instance %s failed: %s", instance_id, exc)
        raise InstanceError(f"Could not write the backup: {exc}") from exc

    (directory / f"{backup_id}{META_SUFFIX}").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    meta["archive_bytes"] = archive.stat().st_size
    meta["pruned"] = prune(instance_id, protect=protect)
    logger.info(
        "Backed up instance %s as %s (%s files, %s bytes)",
        instance_id, backup_id, meta["files"], meta["size_bytes"],
    )
    return meta


def prune(instance_id: int, protect: str | None = None) -> list[str]:
    """Drop everything past the newest KEEP backups; returns what was removed.

    ``protect`` is never dropped, however old it is: the safety copy taken just
    before a restore would otherwise be able to prune the very archive that
    restore is about to unpack (#181).
    """
    backups = list_backups(instance_id)
    dropped = []
    for backup in backups[KEEP:]:
        if backup["id"] == protect:
            continue
        delete_backup(instance_id, backup["id"])
        dropped.append(backup["id"])
    if dropped:
        logger.info("Pruned backups %s for instance %s", ", ".join(dropped), instance_id)
    return dropped


# --------------------------------------------------------------------------- #
# Downloading, deleting
# --------------------------------------------------------------------------- #

def archive_path(instance_id: int, backup_id: str) -> Path:
    _require_id(backup_id)
    archive = backups_dir(instance_id) / f"{backup_id}{ARCHIVE_SUFFIX}"
    if not archive.is_file():
        raise InstanceError("Unknown backup")
    return archive


def download_name(instance_id: int, backup_id: str) -> str:
    """A filename that says what it is on the user's disk, not just when it was."""
    meta = _read_meta(backups_dir(instance_id) / f"{backup_id}{META_SUFFIX}") or {}
    name = str(meta.get("instance_name") or f"instance-{instance_id}")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower() or "instance"
    return f"reforger-save-{slug}-{backup_id}{ARCHIVE_SUFFIX}"


def delete_backup(instance_id: int, backup_id: str) -> None:
    archive = archive_path(instance_id, backup_id)
    archive.unlink(missing_ok=True)
    (backups_dir(instance_id) / f"{backup_id}{META_SUFFIX}").unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Uploading someone else's file
# --------------------------------------------------------------------------- #

def _member_is_safe(member: tarfile.TarInfo) -> bool:
    """Only plain files and directories, only below `profile/`, never upwards.

    An uploaded archive is the one input here that the manager did not write, and
    it is unpacked as root. Symlinks, hard links, device nodes, absolute paths
    and any `..` are refused outright rather than sanitised, so nothing outside
    the instance's own profile can be reached or replaced.
    """
    if not (member.isfile() or member.isdir()):
        return False
    name = member.name.replace("\\", "/").lstrip("./")
    if member.name.startswith("/") or not name:
        return False
    parts = PurePosixPath(name).parts
    if any(part in ("..", "") for part in parts):
        return False
    return parts[0] in ("profile", EMBEDDED_META)


def inspect_upload(path: Path) -> dict:
    """Validate an uploaded archive and read back whatever it says about itself."""
    try:
        with tarfile.open(path, "r:gz") as tar:
            total = files = members = 0
            embedded = None
            for member in tar:
                members += 1
                if members > MAX_UPLOAD_MEMBERS:
                    raise InstanceError("That archive holds far too many files.")
                if not _member_is_safe(member):
                    raise InstanceError(
                        f"That archive contains an entry this manager will not "
                        f"unpack: {member.name}"
                    )
                if member.name == EMBEDDED_META:
                    handle = tar.extractfile(member)
                    raw = handle.read(64_000) if handle else b""
                    try:
                        parsed = json.loads(raw.decode("utf-8"))
                        embedded = parsed if isinstance(parsed, dict) else None
                    except (ValueError, UnicodeDecodeError):
                        embedded = None
                    continue
                if member.isfile():
                    files += 1
                    total += member.size
                    if total > MAX_UPLOAD_BYTES:
                        raise InstanceError("That archive unpacks to more than 2 GB.")
            if not files:
                raise InstanceError(
                    "That archive holds no profile data — it is not a save backup."
                )
            return {"files": files, "size_bytes": total, "embedded": embedded}
    except tarfile.TarError as exc:
        raise InstanceError("That file is not a readable .tar.gz archive.") from exc


def _carried_over(embedded: dict | None) -> dict:
    """The display fields an uploaded archive is allowed to speak for itself with.

    Whatever is inside the file was written by someone else's manager, so it is
    treated as text to show, never as anything to act on: known keys only, typed,
    and clipped.
    """
    if not isinstance(embedded, dict):
        return {}
    out = {}
    for key in ("instance_name", "template_name", "scenario_id", "label", "app_version"):
        value = embedded.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()[:200]
    for key in ("persistence", "server_running"):
        if isinstance(embedded.get(key), bool):
            out[key] = embedded[key]
    if isinstance(embedded.get("hive_id"), int):
        out["hive_id"] = embedded["hive_id"]
    if isinstance(embedded.get("created_at"), str):
        out["origin_created_at"] = embedded["created_at"][:40]
    return out


def store_upload(instance_id: int, filename: str, temp_path: Path) -> dict:
    """Adopt a validated archive onto this instance's shelf as a new backup."""
    with Session(get_engine()) as session:
        if not session.get(Instance, instance_id):
            raise InstanceError("Server not found")
    inspected = inspect_upload(temp_path)

    directory = backups_dir(instance_id)
    directory.mkdir(parents=True, exist_ok=True)
    backup_id = _mint_id(directory)
    archive = directory / f"{backup_id}{ARCHIVE_SUFFIX}"
    shutil.move(str(temp_path), archive)

    carried = _carried_over(inspected["embedded"])
    origin = carried.pop("origin_created_at", None)
    meta = {
        "id": backup_id,
        "created_at": datetime.now(UTC).isoformat(),
        "label": (carried.pop("label", "") or Path(filename or "").name)[:120],
        "source": SOURCE_UPLOAD,
        "instance_id": instance_id,
        "uploaded_filename": Path(filename or "backup.tar.gz").name[:200],
        "origin_created_at": origin,
        "files": inspected["files"],
        "size_bytes": inspected["size_bytes"],
        "skipped": [],
        **carried,
    }
    (directory / f"{backup_id}{META_SUFFIX}").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    meta["archive_bytes"] = archive.stat().st_size
    meta["pruned"] = prune(instance_id)
    logger.info("Adopted uploaded backup %s for instance %s", backup_id, instance_id)
    return meta


# --------------------------------------------------------------------------- #
# Restoring
# --------------------------------------------------------------------------- #

def restore_backup(
    instance_id: int,
    backup_id: str,
    switch_template_id: int | None = None,
    backup_first: bool = False,
    force: bool = False,
) -> dict:
    """Put a backup back: clear the current game state, then unpack the archive.

    Stopped servers only, and the clear is not optional. Unpacking over a live
    world would leave the newer save points that the archive does not contain
    sitting next to the ones it does, and the engine would load whichever it
    likes — a mix of two worlds is not a restore.

    ``switch_template_id`` repoints the instance in the same step (#181). An
    instance outlives its templates, so most of the shelf is usually worlds from
    setups the server no longer runs; restoring one of those without also putting
    the matching template back writes files the running scenario will never read.
    Doing both here is what makes the shelf usable rather than merely honest.

    ``backup_first`` copies the world being replaced onto the shelf beforehand —
    a restore is the one destructive action here that had no undo.

    A restore whose result would not load — wrong scenario, wrong hive id, or an
    archive that never recorded either — is refused unless ``force`` (#184). The
    rule is right almost always and the operator is right the rest of the time:
    a scenario id that changed upstream, a mod that moved its data, a world being
    deliberately transplanted. So the rule is enforced rather than merely drawn in
    the GUI, and there is exactly one documented way past it, which says in the
    log what it did.
    """
    archive = archive_path(instance_id, backup_id)
    with Session(get_engine()) as session:
        if not session.get(Instance, instance_id):
            raise InstanceError("Server not found")
    if instance_service.container_status(instance_id) == "running":
        raise InstanceError("Stop the server before restoring a backup")
    if not docker_service.ping():
        # Making a backup does not need Docker; putting one back does, because
        # the files it replaces are owned by root. Say which of the two is
        # unavailable instead of failing with a generic error.
        raise InstanceError(
            "Docker is not reachable, and restoring needs a helper container to "
            "replace files the server wrote as root. Start Docker and try again."
        )

    # What the instance would be running when the files land, which is what
    # decides whether they can be read: the template being switched to, or the
    # one already attached.
    meta = _read_meta(backups_dir(instance_id) / f"{backup_id}{META_SUFFIX}") or {}
    with Session(get_engine()) as session:
        inst = session.get(Instance, instance_id)
        targets = _template_targets(session)
        current_template_id = inst.template_id
        wanted_id = (
            switch_template_id if switch_template_id is not None else current_template_id
        )
    target = next((t for t in targets if t["id"] == wanted_id), None)
    if target is None:
        raise InstanceError("Template not found")
    fit = fit_of(meta, target)
    if fit != FIT_MATCH and not force:
        raise InstanceError(_misfit_reason(meta, target))
    forced = fit != FIT_MATCH
    if forced:
        # The forced path is the one most likely to be wrong, so its undo is not
        # optional (#184).
        backup_first = True
        logger.warning(
            "Forced restore of backup %s onto instance %s under template %s (%s): %s",
            backup_id, instance_id, target["id"], fit, _misfit_reason(meta, target),
        )

    safety = None
    if backup_first and instance_service._state_paths(_profile_dir(instance_id)):
        # protect=: this copy must not be able to prune the archive being restored.
        safety = create_backup(
            instance_id, "Replaced by a restore", SOURCE_PRE_RESTORE, protect=backup_id
        )
    switched = None
    if switch_template_id is not None and switch_template_id != current_template_id:
        # Before the files, so a failure here leaves the instance untouched
        # rather than holding a world its template cannot read. Repointing an
        # instance at the template it already has would tear its container down
        # for nothing, so that case is skipped.
        instance_service.set_instance_template(instance_id, switch_template_id)
        switched = _instance_context(instance_id)

    idir = _instance_dir(instance_id)
    profile = _profile_dir(instance_id)
    profile.mkdir(parents=True, exist_ok=True)
    replaced = instance_service._state_paths(profile)
    size, files = instance_service._dir_usage(replaced)

    # Every path here was minted by this module or produced by the state scan —
    # nothing a caller sent reaches the shell.
    rel = [p.relative_to(idir).as_posix() for p in replaced]
    script = (
        " ".join(f"rm -rf '/idata/{r}';" for r in rel)
        + f" tar xzf '/idata/backups/{archive.name}' -C /idata"
    )
    host_dir = docker_service.host_path_for(str(idir))
    try:
        docker_service.get_client().containers.run(
            config.settings.steamcmd_image,
            entrypoint="/bin/sh",
            command=["-c", script],
            remove=True,
            volumes={host_dir: {"bind": "/idata", "mode": "rw"}},
            labels={docker_service.LABEL_MANAGED: "true"},
            security_opt=docker_service.SECURITY_OPT,
        )
    except DockerException as exc:
        logger.warning("Restoring backup %s failed: %s", backup_id, exc)
        raise InstanceError(
            "Could not restore the backup. Check the manager log for details."
        ) from exc

    # The metadata the archive carries is left inside the instance dir by tar;
    # it belongs to the backup, not to the profile.
    (idir / EMBEDDED_META).unlink(missing_ok=True)
    logger.info("Restored backup %s onto instance %s", backup_id, instance_id)
    return {
        "restored": {**meta, "id": backup_id},
        "replaced": {"size_bytes": size, "files": files, "paths": rel},
        "switched_to": switched,
        "safety_backup": safety,
        "forced": forced,
        "fit": fit,
    }
