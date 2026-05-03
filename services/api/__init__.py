from typing import Any

from fastapi import FastAPI

from services.api import accounts as accounts_module
from services.api import autoclick as autoclick_module
from services.api import characters as characters_module
from services.api import export as export_module
from services.api import maps as maps_module
from services.api import snapshots as snapshots_module
from services.api import treasury as treasury_module
from services.api import world_ws as world_ws_module
from services.events import WorldStream


def build_app(services: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI(title="tthol-memory", version="0.7.2")
    services = dict(services or {})
    services.setdefault("world_stream", WorldStream())
    app.state.services = services

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(characters_module.router)
    app.include_router(snapshots_module.router)
    app.include_router(accounts_module.router)
    app.include_router(autoclick_module.router)
    app.include_router(export_module.router)
    app.include_router(maps_module.router)
    app.include_router(treasury_module.router)
    app.include_router(world_ws_module.router)
    return app
