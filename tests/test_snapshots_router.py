import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_list_snapshots(client):
    resp = await client.get("/api/snapshots")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert "snapshot_id" in rows[0]


async def test_save_snapshot_returns_saved_flag(client):
    resp = await client.post("/api/snapshots", json={"pid": 1001, "source": "inventory"})
    assert resp.status_code == 200
    assert "saved" in resp.json()


async def test_list_accounts(client):
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
