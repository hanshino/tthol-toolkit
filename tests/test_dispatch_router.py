import pytest
from httpx import ASGITransport, AsyncClient

from services.api import build_app
from services.dispatch import DispatchManager
from services.login_store import LoginStore
from services.snapshot_db import SnapshotDB


class WM:
    def character_name(self, pid):
        return {1: "阿克婭"}.get(pid)

    def live_pids(self):
        return [1, 2]


class Daily:
    def precheck(self, name):
        return ("done", "今日都做完了") if name == "債務居士" else ("run", None)


@pytest.fixture
async def client():
    db = SnapshotDB(":memory:")
    store = LoginStore(
        db, protect=lambda s: b"enc:" + s.encode(), unprotect=lambda b: b[4:].decode()
    )
    dispatch = DispatchManager(
        store,
        flow=None,
        daily=Daily(),
        window_problem=lambda pid: "這個視窗沒有 hook" if pid == 2 else None,
        confirm_character=lambda pid, name: None,
        in_game_as=lambda pid: {1: "阿克婭"}.get(pid),
    )
    app = build_app(
        services={"worker_manager": WM(), "login_store": store, "dispatch_manager": dispatch}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


FORM = {
    "username": "acct2",
    "server": "飛雁山莊(花)",
    "password": "pw-secret",
    "protect": "pp-secret",
}


async def test_add_from_the_game_names_the_character_and_returns_no_secret(client):
    r = await client.put("/api/characters/1/login", json=FORM)
    assert r.status_code == 200
    assert r.json()["character"] == "阿克婭" and r.json()["has_password"] is True
    assert "secret" not in r.text
    listed = await client.get("/api/logins")
    assert "secret" not in listed.text and listed.json()[0]["has_protect"] is True
    mine = await client.get("/api/characters/1/login")
    assert mine.json()["username"] == "acct2"


async def test_an_unlocated_window_cannot_add(client):
    r = await client.put("/api/characters/2/login", json=FORM)
    assert r.status_code == 409


async def test_the_list_only_changes_known_characters(client):
    new = {"character": "路人", "username": "x", "server": "私服"}
    assert (await client.put("/api/logins", json=new)).status_code == 404
    await client.put("/api/characters/1/login", json=FORM)
    edit = {"character": "阿克婭", "username": "acct2", "server": "私服", "enabled": False}
    r = await client.put("/api/logins", json=edit)
    assert r.status_code == 200 and r.json()["has_password"] is True  # kept
    assert r.json()["enabled"] is False
    assert (await client.delete("/api/logins/阿克婭")).json()["ok"] is True


async def test_plan_shows_verdicts_and_window_problems(client):
    await client.put("/api/characters/1/login", json=FORM)
    plan = (await client.get("/api/dispatch/plan")).json()
    assert plan["candidates"][0]["verdict"] == "run"
    assert plan["servers"][0] == "飛雁山莊(花)"
    assert plan["windows"] == [
        {"pid": 1, "name": "阿克婭", "problem": None},
        {"pid": 2, "name": None, "problem": "這個視窗沒有 hook"},
    ]


async def test_export_then_import_with_the_passphrase(client):
    await client.put("/api/characters/1/login", json=FORM)
    r = await client.post("/api/logins/export", json={"passphrase": "a long passphrase"})
    assert (
        r.status_code == 200
        and "secret" not in r.text
        and "attachment" in r.headers["content-disposition"]
    )
    wrong = await client.post(
        "/api/logins/import", json={"data": r.text, "passphrase": "not it at all"}
    )
    assert wrong.status_code == 400 and "匯出密碼不對" in wrong.json()["detail"]
    same = await client.post(
        "/api/logins/import", json={"data": r.text, "passphrase": "a long passphrase"}
    )
    assert same.json() == {"added": 0, "updated": 0, "skipped": 1}  # already here
    short = await client.post("/api/logins/export", json={"passphrase": "short"})
    assert short.status_code == 422


async def test_hook_packets_by_sub_type():
    from services.hook_hub import GAME_PACKET, HookHub

    hub = HookHub(list_pids=lambda: [])
    hub._ingest(1, {"t": "msg", "type": GAME_PACKET, "raw": "4a00"})
    hub._ingest(1, {"t": "msg", "type": GAME_PACKET, "raw": "4a01"})
    hub._ingest(1, {"t": "msg", "type": GAME_PACKET, "raw": "1100"})
    last, seen = hub.packets(1)
    assert last is not None and seen[0x4A][0] == 2 and seen[0x11][0] == 1
    app = build_app(services={"hook_hub": hub})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        body = (await ac.get("/api/characters/1/hook/packets")).json()
    assert {t["sub_type"]: t["count"] for t in body["types"]} == {0x4A: 2, 0x11: 1}
    assert body["last_ago"] is not None and body["connected"] is False
