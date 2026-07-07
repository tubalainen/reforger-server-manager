"""Template CRUD + config.json export (auth-gated)."""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlmodel import Session, select

import auth
from models import Template, get_engine
from services import template_service
from services.template_service import TemplateSpec

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _out(t: Template) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "spec": template_service.spec_from_config(t.config_json),
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


@router.get("")
async def list_templates(_user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        rows = session.exec(select(Template).order_by(Template.name)).all()
        return [
            {"id": t.id, "name": t.name, "description": t.description,
             "updated_at": t.updated_at.isoformat()}
            for t in rows
        ]


@router.post("", status_code=201)
async def create_template(spec: TemplateSpec, _user: str = Depends(auth.require_session)):
    config_json = template_service.render_config_json(spec)
    with Session(get_engine()) as session:
        if session.exec(select(Template).where(Template.name == spec.name)).first():
            raise HTTPException(status_code=409, detail=f"A template named '{spec.name}' already exists")
        t = Template(name=spec.name, description=spec.description, config_json=config_json)
        session.add(t)
        session.commit()
        session.refresh(t)
        return _out(t)


@router.get("/{template_id}")
async def get_template(template_id: int, _user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        return _out(t)


@router.put("/{template_id}")
async def update_template(
    template_id: int, spec: TemplateSpec, _user: str = Depends(auth.require_session)
):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        clash = session.exec(
            select(Template).where(Template.name == spec.name, Template.id != template_id)
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail=f"A template named '{spec.name}' already exists")
        t.name = spec.name
        t.description = spec.description
        t.config_json = template_service.render_config_json(spec)
        t.updated_at = datetime.now(timezone.utc)
        session.add(t)
        session.commit()
        session.refresh(t)
        return _out(t)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: int, _user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        session.delete(t)
        session.commit()


@router.get("/{template_id}/config.json")
async def download_config(template_id: int, _user: str = Depends(auth.require_session)):
    with Session(get_engine()) as session:
        t = session.get(Template, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="Template not found")
        # Re-dump to guarantee pretty output regardless of how it was stored
        pretty = json.dumps(json.loads(t.config_json), indent=2)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in t.name) or "config"
        return Response(
            content=pretty,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe}.json"'},
        )


@router.post("/preview")
async def preview_config(spec: TemplateSpec, _user: str = Depends(auth.require_session)):
    """Render config.json for a spec without saving (live wizard preview)."""
    return spec.to_config()
