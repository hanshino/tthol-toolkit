import logging
import re
import time
import traceback
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from services.api import accounts as accounts_module
from services.api import autoclick as autoclick_module
from services.api import backup as backup_module
from services.api import characters as characters_module
from services.api import keep_active as keep_active_module
from services.api import maps as maps_module
from services.api import snapshots as snapshots_module
from services.api import treasury as treasury_module
from services.api import world_ws as world_ws_module
from services.diag_events import ErrorCode
from services.events import WorldStream

log = logging.getLogger("tthol.api")
_PID_IN_PATH = re.compile(r"/(\d+)(?:/|$)")
SLOW_REQUEST_SECONDS = 1.0


def build_app(services: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI(title="tthol-memory", version="1.0.0")
    services = dict(services or {})
    services.setdefault("world_stream", WorldStream())
    app.state.services = services

    def _pid_from(path: str) -> int | None:
        m = _PID_IN_PATH.search(path)
        return int(m.group(1)) if m else None

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error(
            "unhandled error on %s %s",
            request.method,
            request.url.path,
            extra={
                "cat": "api",
                "code": ErrorCode.E_API_5XX,
                "detail": {
                    "path": request.url.path,
                    "method": request.method,
                    "status": 500,
                    "pid": _pid_from(request.url.path),
                    "traceback": "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ),
                },
            },
        )
        # Match HTTPException's body shape so the frontend has one parse path.
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        emit = log.error if exc.status_code >= 500 else log.warning
        emit(
            "%s %s -> %d",
            request.method,
            request.url.path,
            exc.status_code,
            extra={
                "cat": "api",
                "code": ErrorCode.E_API_5XX if exc.status_code >= 500 else None,
                "detail": {
                    "path": request.url.path,
                    "method": request.method,
                    "status": exc.status_code,
                    "pid": _pid_from(request.url.path),
                    "detail": exc.detail,
                },
            },
        )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.middleware("http")
    async def _slow_requests(request: Request, call_next):
        # uvicorn's access log stays off; only slow requests are worth a line.
        # The 15s/60s scan timeouts surface here on their own.
        started = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - started
        if elapsed >= SLOW_REQUEST_SECONDS:
            log.warning(
                "slow request %s %s took %.1fs",
                request.method,
                request.url.path,
                elapsed,
                extra={
                    "cat": "api",
                    "detail": {
                        "path": request.url.path,
                        "method": request.method,
                        "status": response.status_code,
                        "pid": _pid_from(request.url.path),
                        "elapsed": elapsed,
                    },
                },
            )
        return response

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(characters_module.router)
    app.include_router(snapshots_module.router)
    app.include_router(accounts_module.router)
    app.include_router(autoclick_module.router)
    app.include_router(keep_active_module.router)
    app.include_router(maps_module.router)
    app.include_router(treasury_module.router)
    app.include_router(backup_module.router)
    app.include_router(world_ws_module.router)
    return app
