"""帳號派發: the login list (secrets go in on save, never out) and the dispatch run."""

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from services.api_types import (
    DispatchPlan,
    DispatchRequest,
    DispatchStatus,
    GuardStartResult,
    HookPacketType,
    HookPackets,
    LoginEntry,
    LoginEntryIn,
    LoginExportRequest,
    LoginForm,
    LoginImportRequest,
    LoginImportResult,
    MarkDoneRequest,
    OkResponse,
)
from services.login_screen import KNOWN_SERVERS
from services.login_transfer import TransferError, open_sealed, seal

router = APIRouter(tags=["dispatch"])


def _logins(request: Request):
    store = request.app.state.services.get("login_store")
    if store is None:
        raise HTTPException(status_code=503, detail="login store unavailable")
    return store


def _dispatch(request: Request):
    return request.app.state.services.get("dispatch_manager")


def _name(request: Request, pid: int) -> str | None:
    return request.app.state.services["worker_manager"].character_name(pid)


@router.get("/api/logins", response_model=list[LoginEntry])
async def list_logins(request: Request) -> list[LoginEntry]:
    return _logins(request).list()


@router.put("/api/logins", response_model=LoginEntry)
async def update_login(body: LoginEntryIn, request: Request) -> LoginEntry:
    """Change a listed character (new ones come in through 加入自動登入)."""
    saved = _logins(request).update(body)
    if saved is None:
        raise HTTPException(status_code=404, detail="no such character")
    return saved


@router.post("/api/logins/export")
async def export_logins(body: LoginExportRequest, request: Request) -> Response:
    """The whole list, secrets included, sealed with the user's passphrase."""
    data = seal(_logins(request).export_rows(), body.passphrase)
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="tthol-logins.json"'},
    )


@router.post("/api/logins/import", response_model=LoginImportResult)
async def import_logins(body: LoginImportRequest, request: Request) -> LoginImportResult:
    try:
        rows = open_sealed(body.data, body.passphrase)
    except TransferError as e:
        wrong = "passphrase" in str(e)
        raise HTTPException(
            status_code=400, detail="匯出密碼不對，或檔案損壞" if wrong else "不是帳號匯出檔"
        ) from e
    return LoginImportResult(**_logins(request).import_rows(rows, body.overwrite))


@router.delete("/api/logins/{character}", response_model=OkResponse)
async def delete_login(character: str, request: Request) -> OkResponse:
    if not _logins(request).delete(character):
        raise HTTPException(status_code=404, detail="no such character")
    return OkResponse(ok=True)


@router.get("/api/characters/{pid}/login", response_model=LoginEntry | None)
async def get_character_login(pid: int, request: Request) -> LoginEntry | None:
    name = _name(request, pid)
    return _logins(request).get(name) if name else None


@router.put("/api/characters/{pid}/login", response_model=LoginEntry)
async def save_character_login(pid: int, body: LoginForm, request: Request) -> LoginEntry:
    """加入自動登入: the character name is the one located in this window."""
    name = _name(request, pid)
    if not name:
        raise HTTPException(status_code=409, detail="character not located")
    store = _logins(request)
    old = store.get(name)
    entry = LoginEntryIn(
        character=name,
        username=body.username,
        server=body.server,
        enabled=body.enabled,
        password=body.password,
        protect=body.protect,
        sort=old.sort if old else len(store.list()),
    )
    return store.save(entry)


@router.get("/api/characters/{pid}/hook/packets", response_model=HookPackets)
async def hook_packets(pid: int, request: Request) -> HookPackets:
    hub = request.app.state.services.get("hook_hub")
    if hub is None:
        return HookPackets(connected=False)
    now = time.time()
    last, seen = hub.packets(pid)
    return HookPackets(
        connected=hub.status(pid) is not None,
        last_ago=round(now - last, 1) if last else None,
        types=sorted(
            (
                HookPacketType(sub_type=k, count=c, ago=round(now - t, 1))
                for k, (c, t) in seen.items()
            ),
            key=lambda x: x.ago,
        ),
    )


@router.put("/api/logins/{character}/done", response_model=OkResponse)
async def mark_done(character: str, body: MarkDoneRequest, request: Request) -> OkResponse:
    """標記今日完成: the character's 日常 was finished outside the toolkit."""
    daily = request.app.state.services.get("daily_manager")
    if daily is None:
        raise HTTPException(status_code=503, detail="daily queue unavailable")
    if not daily.mark_done(character, body.done):
        raise HTTPException(status_code=409, detail="日常清單是空的")
    return OkResponse(ok=True)


@router.get("/api/dispatch/plan", response_model=DispatchPlan)
async def dispatch_plan(request: Request) -> DispatchPlan:
    mgr = _dispatch(request)
    if mgr is None:
        return DispatchPlan(servers=list(KNOWN_SERVERS))
    wm = request.app.state.services["worker_manager"]
    candidates, windows = mgr.plan(sorted(wm.live_pids()))
    return DispatchPlan(candidates=candidates, windows=windows, servers=list(KNOWN_SERVERS))


@router.get("/api/dispatch", response_model=DispatchStatus)
async def dispatch_status(request: Request) -> DispatchStatus:
    mgr = _dispatch(request)
    return mgr.status() if mgr is not None else DispatchStatus(running=False)


@router.post("/api/dispatch", response_model=GuardStartResult)
async def dispatch_start(body: DispatchRequest, request: Request) -> GuardStartResult:
    mgr = _dispatch(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="派發未啟用")
    ok, reason = mgr.start(body.characters, body.pids, dry_run=body.dry_run)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/api/dispatch/windows/{pid}", response_model=GuardStartResult)
async def dispatch_join(pid: int, request: Request) -> GuardStartResult:
    """加入派發: a window opened after the start takes from the same queue."""
    mgr = _dispatch(request)
    if mgr is None:
        return GuardStartResult(ok=False, reason="派發未啟用")
    ok, reason = mgr.join(pid)
    return GuardStartResult(ok=ok, reason=reason)


@router.post("/api/dispatch/stop", response_model=OkResponse)
async def dispatch_stop(request: Request) -> OkResponse:
    mgr = _dispatch(request)
    if mgr is not None:
        mgr.stop()
    return OkResponse(ok=True)
