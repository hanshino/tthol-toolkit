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
    assert cache.get(URL) is None  # no size segment
    assert cache.get("https://img.hanshino.dev/512/../x.exe") is None
    assert not (tmp_path / "maps").exists()


def test_minimap_uses_map_pixel_space():
    # Stage 1 莫愁谷入口: 80x60 tiles; exits north -> 2, west -> 3, east -> 44.
    r = client().get("/api/maps/1/minimap")
    assert r.status_code == 200
    m = r.json()
    assert (m["width_px"], m["height_px"], m["tile_px"]) == (3200, 2400, 40)
    assert m["image_url"] == "/api/maps/1/image"
    exits = {w["destinations"][0]["stage_id"]: (w["x"], w["y"]) for w in m["warps"]}
    assert set(exits) == {2, 3, 44}
    assert exits[2][1] < 200  # north edge: top-left origin, not flipped
    assert exits[3][0] < 200  # west edge
    assert exits[44][0] > 2800  # east edge
    for marker in m["npcs"] + m["spawns"]:
        assert 0 <= marker["x"] <= m["width_px"] and 0 <= marker["y"] <= m["height_px"]


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
    placed = _position(
        {"map_name": "地英莊", "stage_id": 1054, "x": 52, "y": 9, "px": 2082, "py": 375}
    )
    assert (placed.stage_id, placed.px, placed.py) == (1054, 2082, 375)
    fresh = _position({"map_name": "地英莊", "stage_id": 1054, "x": -1, "y": -1, "px": 0, "py": 0})
    assert fresh.px is None and fresh.py is None
