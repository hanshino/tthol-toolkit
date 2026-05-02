from fastapi import FastAPI

from services.api import characters as characters_module


def build_app(services=None):
    app = FastAPI(title="tthol-memory", version="0.7.2")
    app.state.services = services or {}

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    app.include_router(characters_module.router)
    return app
