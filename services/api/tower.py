import asyncio

from fastapi import APIRouter, HTTPException, Request

from services.api_types import (
    GuardStartResult,
    OkResponse,
    TowerEstimate,
    TowerSettings,
    TowerStatus,
    TowerView,
)

router = APIRouter(prefix="/api/characters/{pid}/tower", tags=["tower"])


def _mgr(request: Request):
    return request.app.state.services.get("tower_manager")


@router.get("", response_model=TowerView)
async def view(pid: int, request: Request) -> TowerView:
    mgr = _mgr(request)
    if mgr is None:
        return TowerView(status=TowerStatus(running=False))
    return mgr.view(pid)


@router.put("/settings", response_model=TowerSettings)
async def put_settings(pid: int, body: TowerSettings, request: Request) -> TowerSettings:
    mgr = _mgr(request)
    if mgr is None:
        return body
    saved = mgr.save_settings(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.post("/start", response_model=GuardStartResult)
async def start(pid: int, request: Request) -> GuardStartResult:
    mgr = _mgr(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="登塔未啟用")
    ok, reason = mgr.start(pid)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/tidy", response_model=GuardStartResult)
async def tidy(pid: int, request: Request) -> GuardStartResult:
    """寶箱整理 now, without a climb."""
    mgr = _mgr(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="登塔未啟用")
    ok, reason = await asyncio.to_thread(mgr.tidy_now, pid)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)


@router.post("/estimate", response_model=TowerEstimate)
async def estimate(pid: int, request: Request) -> TowerEstimate:
    """Estimate the top floor from the current hit and set it as the stop floor."""
    mgr = _mgr(request)
    if mgr is None:
        return TowerEstimate(ok=False, reason="登塔未啟用")
    return mgr.estimate(pid)
