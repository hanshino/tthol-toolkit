from fastapi import APIRouter, Request

from services.api_types import KeepActiveStatus, OkResponse

router = APIRouter(prefix="/api/characters/{pid}/keep-active", tags=["keep-active"])


@router.post("/start", response_model=OkResponse)
async def start(pid: int, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("keep_active_manager")
    if mgr is not None:
        mgr.start(pid)
    return OkResponse(ok=True)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("keep_active_manager")
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)


@router.get("/status", response_model=KeepActiveStatus)
async def status(pid: int, request: Request) -> KeepActiveStatus:
    mgr = request.app.state.services.get("keep_active_manager")
    if mgr is None:
        return KeepActiveStatus(running=False)
    return mgr.status(pid)
