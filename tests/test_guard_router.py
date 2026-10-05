import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app
from services.guard import Cure, GuardManager, GuardStore, Potion, read_vitals


@pytest.fixture
async def client(tmp_path):
    mgr = GuardManager(
        read_locked=lambda pid, fn: (
            (40, 100, 5, 10) if fn is read_vitals else ({24008: 3}, {24206: 9})
        ),
        character_name=lambda pid: "寒江孤影" if pid == 1 else None,
        store=GuardStore(tmp_path / "guard.json"),
        potions=lambda: {24008: Potion("瓊丹妙露", hp=20, hp_share=True)},
        cures=lambda: {24206: Cure("解毒劑", 19, "中毒")},
        pipe_present=lambda pid: False,
    )
    app = build_app(services={"guard_manager": mgr})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_status_defaults(client):
    body = (await client.get("/api/characters/1/guard")).json()
    assert body["running"] is False
    assert body["hook_cmd"] is False
    assert body["character"] == "寒江孤影"
    assert body["config"]["potion"]["hp_items"] == []
    assert body["vitals"] == {"hp": 40, "hp_max": 100, "mp": 5, "mp_max": 10}


async def test_config_round_trip(client):
    cfg = {
        "potion": {"hp_pct": 60, "mp_pct": 30, "hp_items": [24008], "mp_items": []},
        "cure": {"items": [24206]},
    }
    resp = await client.put("/api/characters/1/guard/config", json=cfg)
    assert resp.status_code == 200
    assert (await client.get("/api/characters/1/guard")).json()["config"] == cfg


async def test_config_needs_a_located_character(client):
    resp = await client.put("/api/characters/2/guard/config", json={"potion": {}})
    assert resp.status_code == 409


async def test_start_without_pipe_is_refused(client):
    body = (await client.post("/api/characters/1/guard/start")).json()
    assert body["ok"] is False and body["reason"]


async def test_potions(client):
    body = (await client.get("/api/characters/1/guard/potions")).json()
    assert body == [
        {
            "item_id": 24008,
            "name": "瓊丹妙露",
            "restores": "hp",
            "bag": 3,
            "pet": 0,
            "icon_url": None,
        }
    ]


async def test_cures(client):
    body = (await client.get("/api/characters/1/guard/cures")).json()
    assert body == [
        {
            "item_id": 24206,
            "name": "解毒劑",
            "group": 19,
            "status": "中毒",
            "bag": 0,
            "pet": 9,
            "icon_url": None,
        }
    ]


async def test_no_manager_is_harmless():
    app = build_app(services=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        assert (await ac.get("/api/characters/1/guard")).json()["running"] is False
        assert (await ac.get("/api/characters/1/guard/potions")).json() == []
        assert (await ac.get("/api/characters/1/guard/cures")).json() == []
