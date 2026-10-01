from types import SimpleNamespace

from services import window_prefs as wp


def scr(x, y, w, h):
    return SimpleNamespace(x=x, y=y, width=w, height=h)


FHD = scr(0, 0, 1920, 1080)


def test_no_saved_uses_default_on_large_screen():
    g = wp.compute_geometry(None, [FHD])
    assert (g.width, g.height, g.x, g.y) == (1280, 800, None, None)
    assert g.min_size == (1024, 700)


def test_default_clamped_to_small_screen():
    g = wp.compute_geometry(None, [scr(0, 0, 1366, 768)])
    assert (g.width, g.height) == (1229, 691)
    assert g.min_size == (1024, 691)


def test_no_screens_falls_back_to_default():
    g = wp.compute_geometry(None, [])
    assert (g.width, g.height, g.x, g.y) == (1280, 800, None, None)


def test_primary_is_screen_at_origin():
    left = scr(-1366, 0, 1366, 768)
    g = wp.compute_geometry(None, [left, FHD])
    assert (g.width, g.height) == (1280, 800)


def test_saved_rect_on_screen_is_used():
    saved = {"width": 1500, "height": 900, "x": 100, "y": 50}
    g = wp.compute_geometry(saved, [FHD])
    assert (g.width, g.height, g.x, g.y) == (1500, 900, 100, 50)


def test_saved_rect_on_secondary_screen_is_used():
    saved = {"width": 1280, "height": 800, "x": -1300, "y": 0}
    g = wp.compute_geometry(saved, [FHD, scr(-1920, 0, 1920, 1080)])
    assert (g.x, g.y) == (-1300, 0)


def test_saved_rect_off_every_screen_uses_default():
    saved = {"width": 1280, "height": 800, "x": 5000, "y": 0}
    g = wp.compute_geometry(saved, [FHD])
    assert (g.width, g.height, g.x, g.y) == (1280, 800, None, None)


def test_saved_rect_barely_visible_uses_default():
    saved = {"width": 1280, "height": 800, "x": 1870, "y": 0}  # 50px visible
    g = wp.compute_geometry(saved, [FHD])
    assert g.x is None


def test_saved_smaller_than_min_is_bumped_up():
    saved = {"width": 600, "height": 400, "x": 0, "y": 0}
    g = wp.compute_geometry(saved, [FHD])
    assert (g.width, g.height) == (1024, 700)


def test_load_missing_file_returns_none(tmp_path):
    assert wp.load_saved(tmp_path / "window.json") is None


def test_load_corrupt_file_returns_none(tmp_path):
    p = tmp_path / "window.json"
    p.write_text("{not json", encoding="utf-8")
    assert wp.load_saved(p) is None


def test_load_missing_keys_returns_none(tmp_path):
    p = tmp_path / "window.json"
    p.write_text('{"width": 1280}', encoding="utf-8")
    assert wp.load_saved(p) is None


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "sub" / "window.json"
    wp.save(p, 1400, 860, 10, 20)
    assert wp.load_saved(p) == {"width": 1400, "height": 860, "x": 10, "y": 20}


def test_remember_saves_window_geometry(tmp_path):
    p = tmp_path / "window.json"
    wp.remember(p, SimpleNamespace(width=1300, height=820, x=5, y=6))
    assert wp.load_saved(p) == {"width": 1300, "height": 820, "x": 5, "y": 6}


def test_remember_skips_minimized_window(tmp_path):
    p = tmp_path / "window.json"
    wp.remember(p, SimpleNamespace(width=160, height=28, x=-32000, y=-32000))
    assert not p.exists()


def test_remember_skips_maximized_window(tmp_path):
    # Saving the maximized rect would reopen a non-maximized, screen-sized window.
    p = tmp_path / "window.json"
    wp.remember(p, SimpleNamespace(width=1920, height=1040, x=-8, y=-8), maximized=True)
    assert not p.exists()


def test_remember_never_raises(tmp_path):
    class Broken:
        @property
        def width(self):
            raise RuntimeError("window gone")

    wp.remember(tmp_path / "window.json", Broken())  # must not raise
