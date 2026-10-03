import json

import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app
from services.damage_capture import DamageRecorderManager, GameData


@pytest.fixture
async def client():
    data = GameData()
    data._loaded = True  # no DB needed: nothing is recorded while live() is None
    mgr = DamageRecorderManager(
        live=lambda pid: None, read_locked=lambda pid, read: None, data=data
    )
    app = build_app(services={"damage_manager": mgr})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    mgr.shutdown()


async def test_status_before_start(client):
    r = await client.get("/api/characters/7/damage/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "idle"
    assert body["events"] == []
    assert body["summary"]["combat_dps"] == 0


async def test_start_pause_clear(client):
    assert (await client.post("/api/characters/7/damage/start")).json()["ok"]
    assert (await client.get("/api/characters/7/damage/status")).json()["status"] in (
        "recording", "waiting",
    )  # fmt: skip
    assert (await client.post("/api/characters/7/damage/stop")).json()["ok"]
    assert (await client.get("/api/characters/7/damage/status")).json()["status"] == "paused"
    assert (await client.post("/api/characters/7/damage/clear")).json()["ok"]
    assert (await client.get("/api/characters/7/damage/status")).json()["status"] == "idle"


async def test_export_is_jsonl_attachment(client):
    r = await client.get("/api/characters/7/damage/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.jsonl"')
    header = json.loads(r.text.splitlines()[0])
    assert header["schema"] == "tthol-damage/1"


async def test_without_manager():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/api/characters/7/damage/status")
        assert r.json()["status"] == "idle"
        assert (await ac.post("/api/characters/7/damage/start")).status_code == 503
