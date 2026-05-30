import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_list_characters_returns_mocks(client):
    resp = await client.get("/api/characters")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    assert rows[0]["pid"] == 1001
    assert rows[0]["link"] == "ok"


async def test_world_snapshot(client):
    resp = await client.get("/api/world")
    assert resp.status_code == 200
    body = resp.json()
    assert "chars" in body and "server_ts" in body


async def test_connect_returns_ok(client):
    resp = await client.post("/api/characters/1001/connect", json={"hp": 120})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
