"""Diagnostics endpoints: live events, summary, verbose toggle, client errors, bundle."""

from __future__ import annotations

import datetime as _dt
import logging
import threading
import time
from dataclasses import asdict

from fastapi import APIRouter, Request, Response

from services import diagnostics
from services.api_types import ClientErrorRequest, DiagEventModel, DiagSummary, VerboseState
from services.diag_bundle import build_bundle, bundle_filename
from services.diag_events import ErrorCode

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

client_log = logging.getLogger("tthol.client")

CLIENT_DEDUP_SECONDS = 5.0
_recent_client_errors: dict[str, float] = {}
_dedup_lock = threading.Lock()


def _should_report(message: str, now: float) -> bool:
    """Collapse identical client errors inside a short window.

    A render loop can fire the same error hundreds of times a second; without
    this it would evict the whole ring buffer.
    """
    with _dedup_lock:
        last = _recent_client_errors.get(message)
        if last is not None and now - last < CLIENT_DEDUP_SECONDS:
            return False
        _recent_client_errors[message] = now
        for key, seen in list(_recent_client_errors.items()):
            if now - seen > CLIENT_DEDUP_SECONDS * 10:
                _recent_client_errors.pop(key, None)
        return True


def _sessions(request: Request) -> list[dict]:
    wm = request.app.state.services.get("worker_manager")
    if wm is None:
        return []
    return [
        {"pid": pid, "name": sess.name, "link": sess.link}
        for pid, sess in getattr(wm, "_sessions", {}).items()
    ]


@router.get("/events", response_model=list[DiagEventModel])
async def events(
    since: float | None = None,
    level: str | None = None,
    pid: int | None = None,
    cat: str | None = None,
    code: str | None = None,
    limit: int = 200,
) -> list[DiagEventModel]:
    found = diagnostics.get_buffer().query(
        since=since, level=level, pid=pid, cat=cat, code=code, limit=limit
    )
    return [DiagEventModel(**asdict(e)) for e in found]


@router.get("/summary", response_model=DiagSummary)
async def summary(request: Request) -> DiagSummary:
    from services.logsetup import current_path
    from services.runtime_info import environment_header

    path = current_path()
    return DiagSummary(
        environment=environment_header(),
        sessions=_sessions(request),
        counts=diagnostics.get_buffer().counts(),
        events_path=str(path) if path else None,
        verbose=diagnostics.is_verbose(),
    )


@router.get("/verbose", response_model=VerboseState)
async def get_verbose() -> VerboseState:
    return VerboseState(verbose=diagnostics.is_verbose())


@router.put("/verbose", response_model=VerboseState)
async def put_verbose(body: VerboseState) -> VerboseState:
    diagnostics.set_verbose(body.verbose)
    return VerboseState(verbose=diagnostics.is_verbose())


@router.post("/client-error", response_model=VerboseState)
async def client_error(body: ClientErrorRequest) -> VerboseState:
    if _should_report(body.message, time.time()):
        client_log.error(
            body.message,
            extra={
                "cat": "client",
                "code": ErrorCode.E_CLIENT,
                "detail": {
                    "url": body.url,
                    "stack": body.stack,
                    "component": body.component,
                    "ua": body.ua,
                },
            },
        )
    return VerboseState(verbose=diagnostics.is_verbose())


@router.get("/bundle")
async def bundle(request: Request) -> Response:
    from services.logsetup import current_path
    from services.runtime_info import environment_header

    blob = build_bundle(
        events_path=current_path() or "events.jsonl",
        header=environment_header(),
        sessions=_sessions(request),
    )
    name = bundle_filename(_dt.datetime.now())
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{name}"'},
    )
