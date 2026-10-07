"""補給: the 補貨清單 settings, a preview, and the 現在補給 button."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from services.api_types import (
    GuardStartResult,
    OkResponse,
    SupplyBuyable,
    SupplyConfig,
    SupplyStatus,
    SupplyView,
)

router = APIRouter(tags=["supply"])


def _mgr(request: Request):
    return request.app.state.services.get("supply_manager")


@router.get("/api/characters/{pid}/supply", response_model=SupplyView)
async def view(pid: int, request: Request) -> SupplyView:
    mgr = _mgr(request)
    if mgr is None:
        return SupplyView(character=None)
    # Reads memory and plans routes: off the event loop.
    return await asyncio.to_thread(mgr.view, pid)


@router.get("/api/characters/{pid}/supply/status", response_model=SupplyStatus)
async def status(pid: int, request: Request) -> SupplyStatus:
    mgr = _mgr(request)
    if mgr is None:
        return SupplyStatus(running=False)
    return mgr.status(pid)


@router.put("/api/characters/{pid}/supply/config", response_model=SupplyConfig)
async def put_config(pid: int, body: SupplyConfig, request: Request) -> SupplyConfig:
    mgr = _mgr(request)
    if mgr is None:
        return body
    saved = mgr.save(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.post("/api/characters/{pid}/supply/start", response_model=GuardStartResult)
async def start(pid: int, request: Request) -> GuardStartResult:
    mgr = _mgr(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="補給未啟用")
    return mgr.start(pid)


@router.post("/api/characters/{pid}/supply/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)


@router.get("/api/supply/items", response_model=list[SupplyBuyable])
async def items(request: Request, q: str = "", pid: int | None = None) -> list[SupplyBuyable]:
    """Items a town NPC sells for 銀兩, matching `q` (name substring). With
    `pid`, a character in a family sees only what its family shop sells."""
    mgr = _mgr(request)
    if mgr is None:
        return []
    return await asyncio.to_thread(mgr.buyables, q, 30, pid)
