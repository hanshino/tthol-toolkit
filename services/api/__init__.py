"""FastAPI app builder.

`build_app(services)` returns a FastAPI instance. `services` is a dict
of singletons (worker_manager, snapshot_db, autoclick_manager, ...);
None during tests/dev to allow stub routers to short-circuit.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def build_app(services: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI(title="tthol-memory", version="0.7.2")
    app.state.services = services or {}

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app
