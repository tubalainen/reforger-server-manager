"""Workshop search / asset / dependency-resolution API (all auth-gated)."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

import auth
from services.workshop_service import WorkshopError, normalize_asset_id, workshop

router = APIRouter(prefix="/api/workshop", tags=["workshop"])


@router.get("/search")
async def search(
    q: str = Query(min_length=1),
    page: int = Query(default=1, ge=1),
    _user: str = Depends(auth.require_session),
):
    try:
        return await asyncio.to_thread(workshop.search, q, page)
    except WorkshopError as exc:
        raise HTTPException(status_code=502, detail=f"Workshop unavailable: {exc}") from exc


@router.get("/asset/{asset_id}")
async def asset(asset_id: str, _user: str = Depends(auth.require_session)):
    if not normalize_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Not a valid Workshop id or URL")
    try:
        return await asyncio.to_thread(workshop.get_asset, asset_id)
    except WorkshopError as exc:
        raise HTTPException(status_code=502, detail=f"Workshop unavailable: {exc}") from exc


@router.get("/resolve/{asset_id}")
async def resolve(asset_id: str, _user: str = Depends(auth.require_session)):
    """Resolve an asset plus its full dependency tree into config.json mods[]."""
    if not normalize_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Not a valid Workshop id or URL")
    try:
        return await asyncio.to_thread(workshop.resolve_dependencies, asset_id)
    except WorkshopError as exc:
        raise HTTPException(status_code=502, detail=f"Workshop unavailable: {exc}") from exc
