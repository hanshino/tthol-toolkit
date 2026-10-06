import asyncio

from fastapi import APIRouter, HTTPException, Request

from services.api_types import (
    DailyBatchRequest,
    DailyQueueConfig,
    DailyStartResult,
    DailyStatus,
    DailySummary,
    DailyView,
    GuardStartResult,
    OkResponse,
)

router = APIRouter(tags=["daily"])


def _mgr(request: Request):
    return request.app.state.services.get("daily_manager")


def _idle() -> DailyStatus:
    return DailyStatus(running=False, card=DailySummary(title="今日清單", state="idle"))


@router.get("/api/characters/{pid}/daily", response_model=DailyView)
async def view(pid: int, request: Request) -> DailyView:
    mgr = _mgr(request)
    if mgr is None:
        return DailyView(config=DailyQueueConfig(modules=[]), modules=[], status=_idle())
    name = request.app.state.services["worker_manager"].character_name(pid)
    config = mgr.config(name) if name else DailyQueueConfig()
    return DailyView(config=config, modules=mgr.modules(), status=mgr.status(pid))


@router.put("/api/characters/{pid}/daily/queue", response_model=DailyQueueConfig)
async def put_queue(pid: int, body: DailyQueueConfig, request: Request) -> DailyQueueConfig:
    mgr = _mgr(request)
    if mgr is None:
        return body
    saved = mgr.save_config(pid, body)
    if saved is None:
        raise HTTPException(status_code=409, detail="character not located")
    return saved


@router.post("/api/characters/{pid}/daily/start", response_model=GuardStartResult)
async def start(pid: int, request: Request) -> GuardStartResult:
    mgr = _mgr(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="日常未啟用")
    ok, reason = mgr.start(pid)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/api/characters/{pid}/daily/stop", response_model=OkResponse)
async def stop(pid: int, request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop(pid)
    return OkResponse(ok=True)


@router.post("/api/daily/start", response_model=list[DailyStartResult])
async def start_batch(body: DailyBatchRequest, request: Request) -> list[DailyStartResult]:
    """Start the ticked characters' queues, all at once.

    Each start asks its hook pipe for `caps`; off the event loop and side by
    side, so one wedged pipe neither stalls the world tick nor the others.
    """
    mgr = _mgr(request)
    if mgr is None:
        return [DailyStartResult(pid=pid, ok=False, reason="日常未啟用") for pid in body.pids]
    results = await asyncio.gather(*(asyncio.to_thread(mgr.start, pid) for pid in body.pids))
    return [
        DailyStartResult(pid=pid, ok=ok, reason=reason)
        for pid, (ok, reason) in zip(body.pids, results)
    ]


@router.post("/api/daily/stop", response_model=OkResponse)
async def stop_all(request: Request) -> OkResponse:
    mgr = _mgr(request)
    if mgr is not None:
        mgr.stop_all()
    return OkResponse(ok=True)
