from httpx import ASGITransport, AsyncClient

from reader import StallItem
from services.api import build_app
from services.market_db import MarketDB


class FakeManager:
    def __init__(self):
        self.modes = {}

    def set_mode(self, pid, mode):
        self.modes[pid] = mode

    def status(self, pid):
        return {
            "mode": self.modes.get(pid, "auto"),
            "active": True,
            "reason": "recording",
            "stage_id": 173,
            "map_name": "成都市集",
            "stalls": [{"seller": "A", "sign": "清倉", "last_recorded": 10.0}],
            "current": {
                "seller": "A",
                "sign": "清倉",
                "open": True,
                "opened_at": 1.0,
                "recorded_at": 1.2,
                "settle_s": 0.2,
                "new": 1,
                "unchanged": 0,
                "changed": 0,
                "gone": [[20100, 99_999_999]],
                "rows": [
                    {
                        "item_id": 33620,
                        "price": 99_999_200,
                        "attrs": '{"inlays":[],"plus":0,"stats":{"hp":9420}}',
                        "count": 160,
                        "status": "new",
                        "old_count": None,
                    }
                ],
            },
            "log": [
                {"t": 1.2, "kind": "recorded", "seller": "A", "text": "新上架 1", "refresh": False}
            ],
            "session": {"stalls": 1, "new": 1, "reads": 1},
        }


def client(services):
    return AsyncClient(
        transport=ASGITransport(app=build_app(services=services)), base_url="http://test"
    )


async def test_status_and_mode():
    mgr = FakeManager()
    async with client({"market_manager": mgr}) as ac:
        r = await ac.put("/api/characters/7/market/mode", json={"mode": "off"})
        assert r.status_code == 200
        body = (await ac.get("/api/characters/7/market/status")).json()
    assert body["mode"] == "off"
    row = body["current"]["rows"][0]
    assert (row["price_kind"], row["coins"], row["silver"]) == ("coin", 200, 200_000_000)
    assert row["stats"] == [{"label": "體力", "value": 9420}]
    gone = body["current"]["gone"][0]
    assert (gone["price_kind"], gone["silver"]) == ("negotiate", None)


async def test_status_without_manager():
    async with client({}) as ac:
        body = (await ac.get("/api/characters/7/market/status")).json()
    assert body["active"] is False and body["stalls"] == []


async def test_bad_mode_rejected():
    async with client({"market_manager": FakeManager()}) as ac:
        r = await ac.put("/api/characters/7/market/mode", json={"mode": "sometimes"})
    assert r.status_code == 422


async def test_items_listings_export(tmp_path):
    db = MarketDB(str(tmp_path / "m.db"))
    it = StallItem(item_id=24004, price=1_000, qty=20, plus=0, stats=(), inlays=())
    db.record("A", "清倉", 173, "成都市集", 0, (it,), now=10)
    async with client({"market_db": db}) as ac:
        items = (await ac.get("/api/market/items")).json()
        assert [(i["item_id"], i["min"]) for i in items] == [(24004, 1_000)]
        assert items[0]["name"]  # resolved from tthol.sqlite
        assert (await ac.get("/api/market/items", params={"q": "不存在的道具"})).json() == []
        listings = (await ac.get("/api/market/items/24004/listings")).json()
        assert listings[0]["seller"] == "A" and listings[0]["count"] == 20
        totals = (await ac.get("/api/market/totals")).json()
        assert totals["listings"] == 1 and totals["visits"] == 1
        csv = await ac.get("/api/market/export.csv")
        assert csv.status_code == 200 and "攤主" in csv.text
    db.close()


async def test_exclusion_endpoints(tmp_path):
    db = MarketDB(str(tmp_path / "m.db"), values={})
    it = StallItem(item_id=24004, price=1_000, qty=20, plus=0, stats=(), inlays=())
    db.record("A", "清倉", 173, "成都市集", 0, (it,), now=10)
    lid = db.listings_for_item(24004)[0]["id"]
    async with client({"market_db": db}) as ac:
        assert (
            await ac.put(f"/api/market/listings/{lid}/excluded", json={"excluded": True})
        ).status_code == 200
        assert (await ac.get("/api/market/items/24004/listings")).json()[0]["excluded"] == "listing"
        r = await ac.put("/api/market/sellers/excluded", json={"seller": "A", "excluded": True})
        assert r.status_code == 200
        assert (await ac.get("/api/market/items/24004/listings")).json()[0]["excluded"] == "seller"
        assert (await ac.get("/api/market/items")).json()[0]["flagged"] == 1
        assert (
            await ac.put("/api/market/listings/999/excluded", json={"excluded": True})
        ).status_code == 404
    db.close()


async def test_market_db_missing():
    async with client({}) as ac:
        assert (await ac.get("/api/market/items")).status_code == 503
