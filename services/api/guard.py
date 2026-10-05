from fastapi import APIRouter, HTTPException, Request

from services.api_types import (
    BuffSkillCandidate,
    GuardConfig,
    GuardStartResult,
    GuardStatus,
    OkResponse,
    PotionCandidate,
)

router = APIRouter(prefix="/api/characters/{pid}/guard", tags=["guard"])


def _mgr(request: Request):
    return request.app.state.services.get("guard_manager")


@router.get("", response_model=GuardStatus)
async def status(pid: int, request: Request) -> GuardStatus:
    mgr = _mgr(request)
    if mgr is None:
        return GuardStatus(running=False, hook_cmd=False)
    return mgr.status(pid)


@router.put("/config", response_model=GuardConfig)
async def put_config(pid: int, body: GuardConfig, request: Request) -> GuardConfig:
    mgr = _mgr(request)
    if mgr is None:
        return body
    saved = mgr.set_config(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.post("/start", response_model=GuardStartResult)
async def start(pid: int, request: Request) -> GuardStartResult:
    mgr = _mgr(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="守護未啟用")
    return mgr.start(pid)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)


@router.get("/potions", response_model=list[PotionCandidate])
async def potions(pid: int, request: Request) -> list[PotionCandidate]:
    mgr = _mgr(request)
    return mgr.potions(pid) if mgr is not None else []


@router.get("/skills", response_model=list[BuffSkillCandidate])
async def skills(pid: int, request: Request) -> list[BuffSkillCandidate]:
    mgr = _mgr(request)
    return mgr.buff_candidates(pid) if mgr is not None else []
