"""分身交貨: a receiver's whitelist, the open receivers, start and stop."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from services.api_types import (
    GuardStartResult,
    HandoffConfig,
    HandoffStart,
    HandoffStatus,
    HandoffView,
    OkResponse,
)

router = APIRouter(prefix="/api/characters/{pid}/handoff", tags=["handoff"])


def _mgr(request: Request):
    return request.app.state.services.get("handoff_manager")


@router.get("", response_model=HandoffView)
async def view(pid: int, request: Request) -> HandoffView:
    mgr = _mgr(request)
    if mgr is None:
        return HandoffView(
            character=None, config=HandoffConfig(), status=HandoffStatus(running=False)
        )
    # Asks the hook for the bag: off the event loop.
    return await asyncio.to_thread(mgr.view, pid)


@router.get("/status", response_model=HandoffStatus)
async def status(pid: int, request: Request) -> HandoffStatus:
    mgr = _mgr(request)
    if mgr is None:
        return HandoffStatus(running=False)
    return mgr.status(pid)


@router.put("/config", response_model=HandoffConfig)
async def put_config(pid: int, body: HandoffConfig, request: Request) -> HandoffConfig:
    mgr = _mgr(request)
    if mgr is None:
        return body
    saved = mgr.save(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.post("/start", response_model=GuardStartResult)
async def start(pid: int, body: HandoffStart, request: Request) -> GuardStartResult:
    mgr = _mgr(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="分身交貨未啟用")
    ok, reason = await asyncio.to_thread(mgr.start, pid, body.role)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)
