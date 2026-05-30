import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app


@pytest.fixture
async def client():
    app = build_app(services=None)  # services not yet wired
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_openapi_schema_serves(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "tthol-memory"
