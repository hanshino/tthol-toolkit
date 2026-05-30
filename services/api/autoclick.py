from fastapi import APIRouter, Request

from services.api_types import (
    AutoClickConfig,
    AutoClickStatus,
    AutoClickTestRequest,
    OkResponse,
)

router = APIRouter(prefix="/api/characters/{pid}/autoclick", tags=["autoclick"])


@router.post("/start", response_model=OkResponse)
async def start(pid: int, config: AutoClickConfig, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return OkResponse(ok=True)
    mgr.start(pid, config)
    return OkResponse(ok=True)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return OkResponse(ok=True)
    mgr.stop(pid)
    return OkResponse(ok=True)


@router.post("/test", response_model=OkResponse)
async def test_click(pid: int, body: AutoClickTestRequest, request: Request) -> OkResponse:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return OkResponse(ok=True)
    mgr.test_click(pid, body.merchant_idx)
    return OkResponse(ok=True)


@router.get("/status", response_model=AutoClickStatus)
async def status(pid: int, request: Request) -> AutoClickStatus:
    mgr = request.app.state.services.get("autoclick_manager")
    if mgr is None:
        return AutoClickStatus(running=False)
    return mgr.status(pid)
