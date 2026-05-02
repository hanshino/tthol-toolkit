import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_autoclick_start(client):
    resp = await client.post(
        "/api/characters/1001/autoclick/start",
        json={"interval_seconds": 60, "merchant_idx": 0},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_autoclick_status(client):
    resp = await client.get("/api/characters/1001/autoclick/status")
    assert resp.status_code == 200
    assert "running" in resp.json()


async def test_export_csv_returns_path(client):
    resp = await client.post("/api/export/csv", json={"mode": "summary"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"].endswith(".csv")
