"""Template categories (#223): the game modes server templates are filed under.

Managed under System › Category management. A category is only a label: it
never changes what a server runs, so even a Prod template's category can be
changed. Deleting one leaves its templates Uncategorised.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, func, select

import auth
from models import Template, TemplateCategory, get_engine
from services import change_log

router = APIRouter(prefix="/api/template-categories", tags=["template-categories"])

NAME_MAX = 60


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)

    @field_validator("name")
    @classmethod
    def _trim(cls, value: str) -> str:
        value = " ".join((value or "").split())
        if not value:
            raise ValueError("A category needs a name")
        return value


def name_of(session: Session, category_id: int | None) -> str | None:
    if category_id is None:
        return None
    row = session.get(TemplateCategory, category_id)
    return row.name if row else None


def require(session: Session, category_id: int | None) -> None:
    """A template may name an existing category, or none."""
    if category_id is not None and session.get(TemplateCategory, category_id) is None:
        raise HTTPException(status_code=400, detail="That category no longer exists")


def change_line(old: str | None, new: str | None) -> tuple[str, str]:
    return ("meta", f"Category: {old or 'Uncategorised'} → {new or 'Uncategorised'}")


def _require_free(session: Session, name: str, exclude_id: int | None = None) -> None:
    # Case-insensitive: "conflict" next to "Conflict" is the same game mode twice.
    query = select(TemplateCategory).where(func.lower(TemplateCategory.name) == name.lower())
    if exclude_id is not None:
        query = query.where(TemplateCategory.id != exclude_id)
    if session.exec(query).first():
        raise HTTPException(status_code=409, detail=f"There is already a category '{name}'")


def _out(row: TemplateCategory, counts: dict[int, int]) -> dict:
    return {"id": row.id, "name": row.name, "template_count": counts.get(row.id, 0)}


def _counts(session: Session) -> dict[int, int]:
    return dict(session.exec(
        select(Template.category_id, func.count())
        .where(Template.category_id.is_not(None))
        .group_by(Template.category_id)
    ).all())


@router.get("")
async def list_categories(_user: str = Depends(auth.require_session)):
    """Every category, by name, with how many templates are filed under it."""
    with Session(get_engine()) as session:
        rows = session.exec(select(TemplateCategory)).all()
        counts = _counts(session)
        return sorted((_out(r, counts) for r in rows), key=lambda c: c["name"].lower())


@router.post("", status_code=201)
async def create_category(body: CategoryIn, _user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        _require_free(session, body.name)
        row = TemplateCategory(name=body.name)
        session.add(row)
        session.commit()
        session.refresh(row)
        return _out(row, {})


@router.put("/{category_id}")
async def rename_category(
    category_id: int, body: CategoryIn, _user: str = Depends(auth.require_session)
):
    with Session(get_engine()) as session:
        row = session.get(TemplateCategory, category_id)
        if not row:
            raise HTTPException(status_code=404, detail="Category not found")
        _require_free(session, body.name, exclude_id=category_id)
        row.name = body.name
        session.add(row)
        session.commit()
        session.refresh(row)
        return _out(row, _counts(session))


@router.delete("/{category_id}")
async def delete_category(category_id: int, _user: str = Depends(auth.require_session)):
    """Delete a category; its templates become Uncategorised, and each one's
    change log says so."""
    with Session(get_engine()) as session:
        row = session.get(TemplateCategory, category_id)
        if not row:
            raise HTTPException(status_code=404, detail="Category not found")
        now = datetime.now(UTC)
        filed = session.exec(select(Template).where(Template.category_id == category_id)).all()
        for t in filed:
            t.category_id = None
            session.add(t)
            change_log.record_lines(
                session, t.id, [("meta", f"Category: {row.name} → Uncategorised (category deleted)")], now
            )
        session.delete(row)
        session.commit()
        return {"uncategorised": len(filed)}
