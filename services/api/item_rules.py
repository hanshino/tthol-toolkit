"""道具處置 table and per-character settings copy."""

from fastapi import APIRouter, HTTPException, Request

from services.api_types import (
    CopySettingsRequest,
    CopySettingsResult,
    ItemRules,
    ItemRulesView,
    SettingsCharacter,
)

router = APIRouter(tags=["settings"])


def _svc(request: Request, key: str):
    return request.app.state.services.get(key)


@router.get("/api/characters/{pid}/item-rules", response_model=ItemRulesView)
async def get_rules(pid: int, request: Request, extra: str = "") -> ItemRulesView:
    """`extra`: comma-separated items.id to describe as well (e.g. warehouse items)."""
    mgr = _svc(request, "guard_manager")
    if mgr is None:
        return ItemRulesView(character=None, rules=ItemRules(), candidates=[])
    ids = {int(x) for x in extra.split(",") if x.strip().isdigit()}
    return mgr.item_view(pid, extra=ids)


@router.put("/api/characters/{pid}/item-rules", response_model=ItemRules)
async def put_rules(pid: int, body: ItemRules, request: Request) -> ItemRules:
    mgr = _svc(request, "guard_manager")
    if mgr is None:
        return body
    saved = mgr.set_items(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.get("/api/settings/characters", response_model=list[SettingsCharacter])
async def settings_characters(request: Request) -> list[SettingsCharacter]:
    db = _svc(request, "snapshot_db")
    if db is None:
        return []
    return [SettingsCharacter(**row) for row in db.settings_characters()]


@router.post("/api/settings/copy", response_model=CopySettingsResult)
async def copy_settings(body: CopySettingsRequest, request: Request) -> CopySettingsResult:
    db = _svc(request, "snapshot_db")
    if db is None:
        return CopySettingsResult(copied=0)
    if body.source == body.target:
        raise HTTPException(status_code=400, detail="source and target are the same character")
    copied = db.copy_settings(body.source, body.target, body.sections)
    mgr = _svc(request, "guard_manager")
    if mgr is not None:
        mgr.reload_settings(body.target)
    return CopySettingsResult(copied=copied)
