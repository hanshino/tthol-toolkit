from httpx import ASGITransport, AsyncClient

from reader import StallItem
from services import market_goto, walk_path
from services.api import build_app
from services.api_types import WalkStatus
from services.market_db import MarketDB


def open_grid(w=40, h=40, blocked=()):
    cells = {(x, y) for x in range(w) for y in range(h)} - set(blocked)
    return walk_path.MapGrid(w, h, cells, [], [])


def test_picks_a_near_tile_on_the_players_side(monkeypatch):
    monkeypatch.setattr(walk_path, "load_grid", lambda sid: open_grid())
    goal, note = market_goto.pick_goal(173, (20, 26), (30, 26), occupied=set())
    assert note is None
    assert goal[0] > 20 and max(abs(goal[0] - 20), abs(goal[1] - 26)) <= market_goto.GOAL_RADIUS


def test_skips_occupied_and_blocked_tiles(monkeypatch):
    blocked = {(21, y) for y in range(24, 29)} | {(22, y) for y in range(24, 29)}
    monkeypatch.setattr(walk_path, "load_grid", lambda sid: open_grid(blocked=blocked))
    goal, _ = market_goto.pick_goal(173, (20, 26), (30, 26), occupied={(19, 26)})
    assert goal not in blocked | {(19, 26), (20, 26)}
    assert max(abs(goal[0] - 20), abs(goal[1] - 26)) <= market_goto.GOAL_RADIUS


def test_near_enough_does_not_walk(monkeypatch):
    monkeypatch.setattr(walk_path, "load_grid", lambda sid: open_grid())
    assert market_goto.pick_goal(173, (20, 26), (21, 26), set()) == ((21, 26), "already there")
    assert market_goto.pick_goal(173, (20, 26), (23, 29), set()) == ((23, 29), "already there")
    assert market_goto.pick_goal(173, (20, 26), (24, 26), set())[1] is None


def test_boxed_in_stall(monkeypatch):
    r = market_goto.GOAL_RADIUS
    around = {(20 + dx, 26 + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)} - {(20, 26)}
    monkeypatch.setattr(walk_path, "load_grid", lambda sid: open_grid(blocked=around))
    assert market_goto.pick_goal(173, (20, 26), (30, 26), set()) == (
        None,
        "no walkable tile near the stall",
    )


class FakeWM:
    def __init__(self, sample):
        self.sample = sample

    def walk_sample(self, pid):
        return self.sample


class FakeWalker:
    def __init__(self):
        self.started = []

    def start(self, pid, goal):
        self.started.append((pid, goal))
        return WalkStatus(state="walking")


def make_db(tmp_path, pos=(20, 26), viewer=(22, 26)):
    db = MarketDB(str(tmp_path / "m.db"), values={})
    it = StallItem(item_id=24004, price=1_000, qty=20, plus=0, stats=(), inlays=())
    db.record("A", "清倉", 173, "成都市集", 0, (it,), now=10, pos=pos, viewer=viewer)
    return db, db.listings_for_item(24004)[0]["id"]


def client(services):
    return AsyncClient(
        transport=ASGITransport(app=build_app(services=services)), base_url="http://test"
    )


async def test_goto_walks_beside_the_stall(tmp_path, monkeypatch):
    monkeypatch.setattr(walk_path, "load_grid", lambda sid: open_grid())
    db, lid = make_db(tmp_path)
    walker = FakeWalker()
    async with client(
        {"market_db": db, "worker_manager": FakeWM((173, 30, 26, 0, 0)), "walk_manager": walker}
    ) as ac:
        r = await ac.post(f"/api/market/listings/{lid}/goto", json={"pid": 7})
    assert r.status_code == 200, r.text
    goal = r.json()["goal"]
    assert max(abs(goal["x"] - 20), abs(goal["y"] - 26)) <= 2
    assert walker.started == [(7, (goal["x"], goal["y"]))]
    db.close()


async def test_goto_other_map_is_refused(tmp_path):
    db, lid = make_db(tmp_path)
    async with client(
        {"market_db": db, "worker_manager": FakeWM((51, 5, 5, 0, 0)), "walk_manager": FakeWalker()}
    ) as ac:
        r = await ac.post(f"/api/market/listings/{lid}/goto", json={"pid": 7})
    assert r.status_code == 409 and "成都市集" in r.json()["detail"]
    db.close()


async def test_goto_falls_back_to_viewer_position(tmp_path):
    db, lid = make_db(tmp_path, pos=None, viewer=(22, 27))
    walker = FakeWalker()
    async with client(
        {"market_db": db, "worker_manager": FakeWM((173, 5, 5, 0, 0)), "walk_manager": walker}
    ) as ac:
        r = await ac.post(f"/api/market/listings/{lid}/goto", json={"pid": 7})
    assert r.json()["note"] == "viewer position"
    assert walker.started == [(7, (22, 27))]
    db.close()


async def test_goto_already_there_does_not_walk(tmp_path, monkeypatch):
    monkeypatch.setattr(walk_path, "load_grid", lambda sid: open_grid())
    db, lid = make_db(tmp_path)
    walker = FakeWalker()
    async with client(
        {"market_db": db, "worker_manager": FakeWM((173, 20, 25, 0, 0)), "walk_manager": walker}
    ) as ac:
        r = await ac.post(f"/api/market/listings/{lid}/goto", json={"pid": 7})
    assert r.json()["note"] == "already there" and walker.started == []
    db.close()


async def test_goto_without_position_or_character(tmp_path):
    db, lid = make_db(tmp_path, pos=None, viewer=None)
    async with client(
        {"market_db": db, "worker_manager": FakeWM((173, 5, 5, 0, 0)), "walk_manager": FakeWalker()}
    ) as ac:
        assert (
            await ac.post(f"/api/market/listings/{lid}/goto", json={"pid": 7})
        ).status_code == 422
        assert (await ac.post("/api/market/listings/999/goto", json={"pid": 7})).status_code == 404
    async with client(
        {"market_db": db, "worker_manager": FakeWM(None), "walk_manager": FakeWalker()}
    ) as ac:
        assert (
            await ac.post(f"/api/market/listings/{lid}/goto", json={"pid": 7})
        ).status_code == 409
    db.close()
