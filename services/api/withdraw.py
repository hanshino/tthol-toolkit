"""領倉白名單: the list, a preview against the account's warehouse, start / stop."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from services.api_types import GuardStartResult, OkResponse, WithdrawConfig, WithdrawView

router = APIRouter(tags=["withdraw"])


def _mgr(request: Request):
    return request.app.state.services.get("withdraw_manager")


@router.get("/api/characters/{pid}/withdraw", response_model=WithdrawView)
async def view(pid: int, request: Request) -> WithdrawView:
    mgr = _mgr(request)
    if mgr is None:
        return WithdrawView(character=None)
    # Asks the hook for the bag and the warehouse: off the event loop.
    return await asyncio.to_thread(mgr.view, pid)


@router.put("/api/characters/{pid}/withdraw/config", response_model=WithdrawConfig)
async def put_config(pid: int, body: WithdrawConfig, request: Request) -> WithdrawConfig:
    mgr = _mgr(request)
    if mgr is None:
        return body
    saved = mgr.save(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.post("/api/characters/{pid}/withdraw/start", response_model=GuardStartResult)
async def start(pid: int, request: Request) -> GuardStartResult:
    mgr = _mgr(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="領倉未啟用")
    return await asyncio.to_thread(mgr.start, pid)


@router.post("/api/characters/{pid}/withdraw/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)
