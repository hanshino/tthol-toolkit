import logging

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from services import diagnostics, logsetup
from services.api import build_app
from services.diag_events import ErrorCode


@pytest.fixture(autouse=True)
def _bus(tmp_path, monkeypatch):
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    logsetup._reset_for_tests()
    monkeypatch.setattr(logsetup, "candidate_paths", lambda: [tmp_path / "events.jsonl"])
    diagnostics.init(console=False)
    diagnostics.get_buffer().clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)
    logsetup._reset_for_tests()


@pytest.fixture
async def client():
    app = build_app(services=None)

    @app.get("/api/_boom")
    async def boom():
        raise RuntimeError("kaboom")

    @app.get("/api/_teapot/{pid}")
    async def teapot(pid: int):
        raise HTTPException(status_code=418, detail="short and stout")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_unhandled_exception_is_logged_and_shaped_like_http_exception(client):
    resp = await client.get("/api/_boom")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal Server Error"}

    events = diagnostics.get_buffer().query(code=ErrorCode.E_API_5XX)
    assert len(events) == 1
    assert events[0].level == "ERROR"
    assert events[0].detail["path"] == "/api/_boom"
    assert events[0].detail["method"] == "GET"
    assert "kaboom" in events[0].detail["traceback"]


async def test_4xx_logs_at_warning_with_the_pid(client):
    resp = await client.get("/api/_teapot/27160")
    assert resp.status_code == 418

    events = [e for e in diagnostics.get_buffer().query(cat="api") if e.level == "WARNING"]
    assert len(events) == 1
    assert events[0].detail["status"] == 418
    assert events[0].detail["pid"] == 27160
    assert events[0].detail["detail"] == "short and stout"


async def test_successful_fast_request_logs_nothing(client):
    await client.get("/api/health")
    assert diagnostics.get_buffer().query(cat="api") == []
