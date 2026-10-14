import asyncio

from fastapi import APIRouter, HTTPException, Request

from services.api_types import (
    DungeonSettings,
    DungeonStatus,
    DungeonView,
    GuardStartResult,
    OkResponse,
)

router = APIRouter(prefix="/api/characters/{pid}/dungeon/{key}", tags=["dungeon"])


def _mgr(request: Request, key: str):
    return (request.app.state.services.get("dungeon_managers") or {}).get(key)


@router.get("", response_model=DungeonView)
async def view(pid: int, key: str, request: Request) -> DungeonView:
    mgr = _mgr(request, key)
    if mgr is None:
        raise HTTPException(status_code=404, detail="unknown dungeon")
    return await asyncio.to_thread(mgr.view, pid)


@router.get("/status", response_model=DungeonStatus)
async def status(pid: int, key: str, request: Request) -> DungeonStatus:
    mgr = _mgr(request, key)
    if mgr is None:
        raise HTTPException(status_code=404, detail="unknown dungeon")
    return mgr.status(pid)


@router.put("/settings", response_model=DungeonSettings)
async def put_settings(
    pid: int, key: str, body: DungeonSettings, request: Request
) -> DungeonSettings:
    mgr = _mgr(request, key)
    if mgr is None:
        raise HTTPException(status_code=404, detail="unknown dungeon")
    saved = mgr.save_settings(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.post("/start", response_model=GuardStartResult)
async def start(pid: int, key: str, request: Request) -> GuardStartResult:
    mgr = _mgr(request, key)
    if mgr is None:
        return GuardStartResult(ok=False, reason="沒有這個副本")
    ok, reason = await asyncio.to_thread(mgr.start, pid)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, key: str, request: Request) -> OkResponse:
    mgr = _mgr(request, key)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)
