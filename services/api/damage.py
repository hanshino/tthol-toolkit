"""Damage capture endpoints: start / pause / clear a recording, poll it, export JSONL."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from services.api_types import DamageStatus, OkResponse
from services.backup import APP_VERSION
from services.damage_capture import summarize

router = APIRouter(prefix="/api/characters/{pid}/damage", tags=["damage"])


def _mgr(request: Request):
    mgr = request.app.state.services.get("damage_manager")
    if mgr is None:
        raise HTTPException(status_code=503, detail="damage recorder unavailable")
    return mgr


@router.post("/start", response_model=OkResponse)
def start(pid: int, request: Request) -> OkResponse:
    _mgr(request).start(pid)
    return OkResponse(ok=True)


@router.post("/stop", response_model=OkResponse)
def stop(pid: int, request: Request) -> OkResponse:
    _mgr(request).stop(pid)
    return OkResponse(ok=True)


@router.post("/clear", response_model=OkResponse)
def clear(pid: int, request: Request) -> OkResponse:
    _mgr(request).clear(pid)
    return OkResponse(ok=True)


@router.get("/status", response_model=DamageStatus)
def status(pid: int, request: Request, since: int = Query(0, ge=0)) -> DamageStatus:
    mgr = request.app.state.services.get("damage_manager")
    if mgr is None:
        return DamageStatus(status="idle", elapsed=0, seq=0, events=[], summary=summarize([], 0))
    return mgr.status(pid, since)


@router.get("/export", responses={200: {"content": {"application/x-ndjson": {}}}})
def export(pid: int, request: Request) -> Response:
    lines = _mgr(request).export_lines(pid, APP_VERSION)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content="\n".join(lines) + "\n",
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="tthol-damage-{ts}.jsonl"'},
    )
