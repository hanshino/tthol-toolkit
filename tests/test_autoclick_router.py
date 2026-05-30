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
        json={"interval_ms": 500, "merchant_idx": 0},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_autoclick_start_with_mode(client):
    """全部收下 / 全部銷毀 modes accept clicks_per_round."""
    for mode in ("off", "collect", "destroy"):
        resp = await client.post(
            "/api/characters/1001/autoclick/start",
            json={
                "interval_ms": 500,
                "merchant_idx": 1,
                "mode": mode,
                "clicks_per_round": 10,
            },
        )
        assert resp.status_code == 200, (mode, resp.text)
        assert resp.json()["ok"] is True

    # Unknown mode is rejected.
    bad = await client.post(
        "/api/characters/1001/autoclick/start",
        json={"interval_ms": 500, "merchant_idx": 0, "mode": "burn"},
    )
    assert bad.status_code == 422


async def test_autoclick_status(client):
    resp = await client.get("/api/characters/1001/autoclick/status")
    assert resp.status_code == 200
    assert "running" in resp.json()
