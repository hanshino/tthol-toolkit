import asyncio

from fastapi import APIRouter, HTTPException, Request

from services.api_types import GrindSettings, GrindStatus, GrindView, GuardStartResult, OkResponse

router = APIRouter(prefix="/api/characters/{pid}/grind", tags=["grind"])


def _mgr(request: Request):
    return request.app.state.services.get("grind_manager")


@router.get("", response_model=GrindView)
async def view(pid: int, request: Request) -> GrindView:
    mgr = _mgr(request)
    if mgr is None:
        return GrindView(status=GrindStatus(running=False))
    return await asyncio.to_thread(mgr.view, pid)


@router.get("/status", response_model=GrindStatus)
async def status(pid: int, request: Request) -> GrindStatus:
    mgr = _mgr(request)
    if mgr is None:
        return GrindStatus(running=False)
    return mgr.status(pid)


@router.put("/settings", response_model=GrindSettings)
async def put_settings(pid: int, body: GrindSettings, request: Request) -> GrindSettings:
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
        return GuardStartResult(ok=False, reason="打怪未啟用")
    ok, reason = await asyncio.to_thread(mgr.start, pid)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)
