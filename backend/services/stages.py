"""Release stages for server templates and mod lists (#223).

Every template and mod list sits in one of three stages:

    test  free to change
    dev   change management: still editable, saves carry an optional comment
    prod  golden and locked: only a release changes it; copy or delete only

Things only move upwards. A Dev item is released to Prod either as a new Prod
item (it becomes v1 in place) or as the next version of an existing Prod item,
in which case the Dev row is used up: its content becomes that item's head,
its change log moves over, and the servers on it follow. Each release also
writes a frozen version row, so an older version can be read back later.

A Prod row's own columns always equal its latest version, so everything that
reads a template keeps reading the row and is right for "latest".
"""
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from models import (
    Instance,
    ModTemplate,
    ModTemplateVersion,
    Template,
    TemplateVersion,
)
from services import change_log, edit_locks

logger = logging.getLogger("manager.stages")

TEST, DEV, PROD = "test", "dev", "prod"
STAGES = (TEST, DEV, PROD)
NEXT = {TEST: DEV, DEV: PROD}
LABELS = {TEST: "Test", DEV: "Dev", PROD: "Prod"}

NOTE_MAX = 500


class StageError(ValueError):
    """A stage move or edit that isn't allowed; `status` is the HTTP answer."""

    status = 400


class ProdLockedError(StageError):
    status = 409


class LockedError(StageError):
    """Someone else has the item open in an editor."""

    status = 423


@dataclass(frozen=True)
class _Kind:
    model: type
    version_model: type
    fk: str            # the version row's column pointing at the item
    content: tuple     # columns a release copies and a version freezes
    noun: str
    snapshot: object
    diff: object
    append: object
    move_log: object


TEMPLATES = _Kind(
    model=Template,
    version_model=TemplateVersion,
    fk="template_id",
    content=(
        "description", "config_json", "scenario_name", "scenario_player_count",
        "launch_params_json", "mods_json", "extras_json",
    ),
    noun="template",
    snapshot=change_log.snapshot,
    diff=change_log.diff,
    append=change_log.record_lines,
    move_log=change_log.move_template_log,
)

MOD_LISTS = _Kind(
    model=ModTemplate,
    version_model=ModTemplateVersion,
    fk="mod_template_id",
    content=("description", "mods_json"),
    noun="mod list",
    snapshot=change_log.mod_template_snapshot,
    diff=change_log.mod_template_diff,
    append=change_log.record_mod_template_lines,
    move_log=change_log.move_mod_template_log,
)


class Promote(BaseModel):
    """Move a template or mod list one stage up. `into_id` releases a Dev one
    as the next version of that Prod one instead of as a new one."""

    note: str = Field("", max_length=NOTE_MAX)
    into_id: int | None = None


class CopyFrom(BaseModel):
    """Which released version to copy (#223); None = the item as it is now."""

    version: int | None = None


def content_of(session: Session, kind: _Kind, row, version: int | None):
    """The row itself, or one of its released versions (StageError if none)."""
    if version is None or (row.stage == PROD and version == row.prod_version):
        return row
    frozen = version_row(session, kind, row.id, version) if row.stage == PROD else None
    if frozen is None:
        raise StageError(f"'{row.name}' has no v{version}")
    return frozen


def stage_fields(row, names: dict[int, str]) -> dict:
    """Where a template or mod list stands in the release flow, for the API."""
    based_on = None
    if row.based_on_id is not None:
        based_on = {
            "id": row.based_on_id,
            "name": names.get(row.based_on_id),
            "version": row.based_on_version,
        }
    return {"stage": row.stage, "prod_version": row.prod_version, "based_on": based_on}


def names_of(session: Session, model) -> dict[int, str]:
    return dict(session.exec(select(model.id, model.name)).all())


def copied_from(src, version: int | None = None) -> str:
    """The opening line of a copy's change log: "Copied from 'X' v2 (Prod)"."""
    number = (version or src.prod_version) if src.stage == PROD else None
    tag = f" v{number}" if number else ""
    return f"Copied from '{src.name}'{tag} ({LABELS.get(src.stage, src.stage)})"


def ensure_editable(row) -> None:
    """The Prod lock: the one check every writer of a template or mod list makes."""
    if row.stage == PROD:
        raise ProdLockedError(
            f"'{row.name}' is in Prod and can't be changed. "
            "Copy it to Test to make changes."
        )


def clean_note(note: str | None) -> str:
    return (note or "").strip()[:NOTE_MAX]


def note_items(note: str | None) -> list[tuple[str, str]]:
    """The change-log line a save comment or release note becomes, if any."""
    note = clean_note(note)
    return [("note", note)] if note else []


def based_on_for(src, version: int | None = None) -> tuple[int | None, int | None]:
    """What a copy of `src` is based on: `src` itself when it's Prod (at the
    version copied), otherwise whatever `src` was based on — so a copy of a copy
    still releases by default as the next version of the Prod item it all
    started from."""
    if src.stage == PROD:
        return src.id, version or src.prod_version
    return src.based_on_id, src.based_on_version


def versions(session: Session, kind: _Kind, item_id: int) -> list:
    """An item's version rows, newest first."""
    vm = kind.version_model
    return list(session.exec(
        select(vm).where(getattr(vm, kind.fk) == item_id).order_by(vm.version.desc())
    ).all())


def version_counts(session: Session, kind: _Kind) -> dict[int, int]:
    vm = kind.version_model
    fk = getattr(vm, kind.fk)
    return dict(session.exec(select(fk, func.count()).group_by(fk)).all())


def _freeze(session: Session, kind: _Kind, row, version: int, note: str, when) -> None:
    session.add(kind.version_model(**{
        kind.fk: row.id, "version": version, "released_at": when, "note": note,
        **{col: getattr(row, col) for col in kind.content},
    }))


def _next_version(session: Session, kind: _Kind, item_id: int) -> int:
    vm = kind.version_model
    top = session.exec(
        select(func.max(vm.version)).where(getattr(vm, kind.fk) == item_id)
    ).one()
    return (top or 0) + 1


def promote(
    session: Session,
    kind: _Kind,
    row,
    *,
    note: str | None = "",
    into_id: int | None = None,
    client_id: str = "",
):
    """Move `row` one stage up and return the item it ends up as.

    Test → Dev changes the stage. Dev → Prod releases it: as a new Prod item
    when `into_id` is None, otherwise as the next version of Prod item `into_id`
    (then the Dev row is gone and the returned item has a different id).
    Nothing bumps updated_at unless what a server runs actually changes.
    """
    if row.stage == PROD:
        raise StageError(f"'{row.name}' is already in Prod")
    if kind is TEMPLATES and edit_locks.held_by_other(row.id, client_id):
        raise LockedError(
            f"'{row.name}' is open in another editor. Close it there first."
        )
    note = clean_note(note)
    now = datetime.now(UTC)

    if row.stage == TEST:
        row.stage = DEV
        session.add(row)
        kind.append(session, row.id, [("release", "Moved from Test to Dev")] + note_items(note), now)
        return row

    if row.stage != DEV:
        raise StageError(f"'{row.name}' has an unknown stage '{row.stage}'")

    if into_id is None:
        # A new Prod item: the same row, so its id, name, log and servers stay.
        row.stage = PROD
        row.prod_version = 1
        row.based_on_id = row.based_on_version = None
        session.add(row)
        _freeze(session, kind, row, 1, note, now)
        kind.append(session, row.id, [("release", "Released to Prod as v1")] + note_items(note), now)
        if kind is TEMPLATES:
            edit_locks.drop(row.id)
        return row

    target = session.get(kind.model, into_id)
    if target is None or target.id == row.id or target.stage != PROD:
        raise StageError(f"Pick a Prod {kind.noun} to release '{row.name}' into")

    before = kind.snapshot(target)
    for col in kind.content:
        setattr(target, col, getattr(row, col))
    version = _next_version(session, kind, target.id)
    target.prod_version = version
    target.updated_at = now
    session.add(target)
    _freeze(session, kind, target, version, note, now)

    # The Dev history becomes the story between the previous version and this.
    kind.move_log(session, row.id, target.id, f"In '{row.name}'")
    # The Prod item keeps its own name; what moved is the content.
    kind.append(
        session, target.id,
        [("release", f"Released as v{version} from '{row.name}' (Dev)")]
        + note_items(note) + kind.diff(before, kind.snapshot(target)),
        now,
    )
    if kind is TEMPLATES:
        for inst in session.exec(select(Instance).where(Instance.template_id == row.id)).all():
            inst.template_id = target.id
            session.add(inst)
        edit_locks.drop(row.id)
    session.flush()
    session.delete(row)
    return target


def version_row(session: Session, kind: _Kind, item_id: int, version: int):
    vm = kind.version_model
    return session.exec(
        select(vm).where(getattr(vm, kind.fk) == item_id, vm.version == version)
    ).first()


def version_list(session: Session, kind: _Kind, ids: list[int] | None = None) -> dict[int, list]:
    """{item id: [{version, released_at, note}, newest first]} for the pickers."""
    vm = kind.version_model
    fk = getattr(vm, kind.fk)
    query = select(vm).order_by(vm.version.desc())
    if ids is not None:
        query = query.where(fk.in_(ids))
    out: dict[int, list] = {}
    for v in session.exec(query).all():
        out.setdefault(getattr(v, kind.fk), []).append({
            "version": v.version,
            "released_at": (
                v.released_at if v.released_at.tzinfo else v.released_at.replace(tzinfo=UTC)
            ).isoformat(),
            "released": change_log.format_local(
                v.released_at if v.released_at.tzinfo else v.released_at.replace(tzinfo=UTC)
            )[:10],
            "note": v.note or "",
        })
    return out


def require_pinnable(session: Session, template: Template, version: int | None) -> None:
    """A server may be pinned to an existing version of a Prod template only."""
    if version is None:
        return
    if template.stage != PROD:
        raise StageError(
            f"'{template.name}' is not in Prod, so it has no versions to pick from"
        )
    if version_row(session, TEMPLATES, template.id, version) is None:
        raise StageError(f"'{template.name}' has no v{version}")


# --------------------------------------------------------------------------- #
# What a server runs: its template at latest, or a pinned Prod version
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RunTarget:
    """The template content one server runs.

    `content` is the live template row (Test, Dev, or Prod at latest) or the
    frozen TemplateVersion it is pinned to; both carry the same columns.
    """

    template: Template
    content: object
    pinned: int | None

    @property
    def running_version(self) -> int | None:
        if self.template.stage != PROD:
            return None
        return self.pinned or self.template.prod_version

    @property
    def label(self) -> str:
        """The template as servers and the Supervisor name it: "Name v3" in Prod."""
        version = self.running_version
        return f"{self.template.name} v{version}" if version else self.template.name

    @property
    def changed_at(self):
        """When what this server runs last changed; None when pinned (frozen)."""
        return None if self.pinned else self.template.updated_at


def run_targets(session: Session, instances) -> dict[int, RunTarget]:
    """What each instance runs, in two queries however many there are."""
    instances = list(instances)
    template_ids = {i.template_id for i in instances}
    templates = {
        t.id: t for t in session.exec(select(Template).where(Template.id.in_(template_ids))).all()
    }
    pins = {(i.template_id, i.template_version) for i in instances if i.template_version}
    frozen = {}
    if pins:
        rows = session.exec(
            select(TemplateVersion).where(
                TemplateVersion.template_id.in_({tid for tid, _ in pins})
            )
        ).all()
        frozen = {(v.template_id, v.version): v for v in rows}
    out: dict[int, RunTarget] = {}
    for inst in instances:
        template = templates.get(inst.template_id)
        if template is None:
            continue
        pin = inst.template_version if template.stage == PROD else None
        content = template
        if pin:
            content = frozen.get((template.id, pin))
            if content is None:
                # Can't happen through the API (versions are never deleted on
                # their own); run latest rather than refuse to start.
                logger.warning(
                    "Server %s is pinned to v%s of '%s', which does not exist; running latest",
                    inst.name, pin, template.name,
                )
                content, pin = template, None
        out[inst.id] = RunTarget(template=template, content=content, pinned=pin)
    return out


def run_target(session: Session, inst) -> RunTarget | None:
    return run_targets(session, [inst]).get(inst.id)


def delete_versions(session: Session, kind: _Kind, item_id: int) -> int:
    """Remove an item's versions and forget it as anyone's origin (on delete)."""
    rows = versions(session, kind, item_id)
    for v in rows:
        session.delete(v)
    for copy in session.exec(select(kind.model).where(kind.model.based_on_id == item_id)).all():
        copy.based_on_id = copy.based_on_version = None
        session.add(copy)
    return len(rows)
