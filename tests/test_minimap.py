"""Tests for the minimap API: map pixel geometry, markers and the thumbnail cache."""

from fastapi.testclient import TestClient

from services import map_db, map_image_cache
from services.char_session import _position
from services.map_image_cache import MapImageCache, thumbnail_url

WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
URL = "https://img.hanshino.dev/g3zc0h.webp"


def client():
    from services.api import build_app

    return TestClient(build_app())


def test_thumbnail_url_inserts_size():
    assert thumbnail_url(URL, 1024) == "https://img.hanshino.dev/1024/g3zc0h.webp"


def test_map_cache_names_by_size_and_hash(tmp_path):
    cache = MapImageCache(tmp_path, fetch=lambda _u: WEBP)
    assert cache.get(thumbnail_url(URL, 512)) == WEBP
    assert (tmp_path / "512_g3zc0h.webp").read_bytes() == WEBP


def test_map_cache_refuses_non_webp_and_odd_urls(tmp_path):
    assert MapImageCache(tmp_path, fetch=lambda _u: b"<html>").get(thumbnail_url(URL, 512)) is None
    cache = MapImageCache(tmp_path / "maps", fetch=lambda _u: WEBP)
    assert cache.get("https://img.hanshino.dev/512/../x.exe") is None
    assert cache.get("https://img.hanshino.dev/512/evil.png") is None
    assert not (tmp_path / "maps").exists()


def test_minimap_uses_map_pixel_space():
    # Stage 1 莫愁谷入口: 80x60 tiles; exits north -> 2, west -> 3, east -> 44.
    r = client().get("/api/maps/1/minimap")
    assert r.status_code == 200
    m = r.json()
    assert (m["width_px"], m["height_px"], m["tile_px"]) == (3200, 2400, 40)
    assert m["image_url"] == "/api/maps/1/image"
    exits = {e["options"][0]["stage_id"]: (e["x"], e["y"]) for e in m["exits"]}
    assert set(exits) == {2, 3, 44}
    assert exits[2][1] > 2200  # north edge: game y grows upward
    assert exits[3][0] < 200  # west edge
    assert exits[44][0] > 2800  # east edge
    for marker in m["npcs"] + m["spawns"]:
        assert 0 <= marker["x"] <= m["width_px"] and 0 <= marker["y"] <= m["height_px"]


def test_exits_come_only_from_map_event_warps():
    # 成都少城: the legacy mpc_sec3 scan also "finds" 莫愁谷入口 (#1), and NPC
    # dialogue warps (慕千影居, 天外密道) have no spot on the map; none is an exit.
    m = client().get("/api/maps/53/minimap").json()
    dests = sorted(o["stage_id"] for e in m["exits"] for o in e["options"])
    assert dests == [10, 19, 27, 32, 54, 173, 173, 733]
    assert all(e["parts"] == 1 and e["prompt"] is None for e in m["exits"])


def test_exit_tag_split_into_zones_shares_its_menu():
    # sestage 1827: tag 256 covers two separate zones, both opening one menu.
    exits = [
        e for e in client().get("/api/maps/1827/minimap").json()["exits"] if e["event_tag"] == 256
    ]
    assert [e["part"] for e in exits] == [1, 2] and {e["parts"] for e in exits} == {2}
    assert exits[0]["prompt"] == "要回到流星村火島何處呢？"
    options = {o["label"]: o for o in exits[0]["options"]}
    assert options["回到流星冰島˙南"]["stage_id"] == 57 and options["回到流星冰島˙南"]["landed"]
    assert options["前往莫愁谷"]["stage_id"] == 1 and not options["前往莫愁谷"]["landed"]


def test_game_text_plain_strips_markup():
    from services.map_db import game_text_plain

    raw = "<FONT COLOR=F88900（旁白\\n）</FONT>好　　的"  # literal \n, missing '>'
    assert game_text_plain(raw) == "（旁白\n）好\n的"
    assert game_text_plain("") is None


def test_region_picks_the_space_the_player_is_in():
    # Stage 53 成都少城: the city fills the top of the image, interiors sit below.
    # Game y grows upward, so the city is the high-y box.
    c = client()
    city = c.get("/api/maps/53/region?x=67&y=151").json()
    assert city["y1"] > city["y0"] > 3000 and city["x1"] - city["x0"] > 4000
    room = c.get("/api/maps/53/region?x=60&y=20").json()
    assert room["y1"] < 2000 and room["x1"] - room["x0"] < 2000
    assert c.get("/api/maps/53/region?x=0&y=0").json() is None  # blocked corner


def test_region_snaps_to_a_nearby_walkable_cell():
    from services.map_regions import _label

    # 14x5 map, walkable only on the second image row (game y=3); game y=0 is
    # the bottom row.
    reg = _label(14, 5, "0" * 14 + "1" * 14 + "0" * 42)
    assert reg.at(5, 3) == 0
    assert reg.at(5, 4) == 0  # one row above the path: snapped
    assert reg.at(5, 0) is None  # three rows off: too far


def test_minimap_unknown_stage_404():
    assert client().get("/api/maps/999999/minimap").status_code == 404


def test_minimap_image(monkeypatch, tmp_path):
    seen = []
    cache = MapImageCache(tmp_path, fetch=lambda u: seen.append(u) or WEBP)
    monkeypatch.setattr(map_image_cache, "_cache", cache)
    monkeypatch.setattr(map_db, "minimap_base", lambda sid: {"url": URL} if sid == 1 else None)
    c = client()
    r = c.get("/api/maps/1/image?size=512")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert seen == ["https://img.hanshino.dev/512/g3zc0h.webp"]
    assert c.get("/api/maps/1/image?size=300").status_code == 422
    assert c.get("/api/maps/2/image").status_code == 404


def test_position_hides_pixels_until_placed():
    placed = _position({"map_name": "地英莊", "stage_id": 1054, "x": 52, "y": 9})
    assert (placed.stage_id, placed.px, placed.py) == (1054, 2100, 380)
    fresh = _position({"map_name": "地英莊", "stage_id": 1054, "x": -1, "y": -1})
    assert fresh.px is None and fresh.py is None


def test_position_ignores_move_target_pixels():
    # Mid-walk sample from a live probe: the struct's pixel pair already holds
    # the click destination while the tiles are still at (64, 142).
    pos = _position({"stage_id": 53, "x": 64, "y": 142, "px": 1860, "py": 5039})
    assert (pos.px, pos.py) == (64 * 40 + 20, 142 * 40 + 20)


def test_minimap_image_falls_back_to_original(monkeypatch, tmp_path):
    # PictShare answers 500 for resizes of very large maps; the original still serves.
    def fetch(u):
        if "/1024/" in u:
            raise OSError("HTTP Error 500")
        return WEBP

    monkeypatch.setattr(map_image_cache, "_cache", MapImageCache(tmp_path, fetch=fetch))
    assert map_image_cache.get(URL, 1024) == WEBP
    assert (tmp_path / "full_g3zc0h.webp").exists()
