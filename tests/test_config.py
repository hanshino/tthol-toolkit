"""Tests for gui.config — theme preference persistence."""

import json
from gui.config import load_theme, save_theme


def test_load_theme_returns_dark_when_no_file(tmp_path):
    cfg = tmp_path / "config.json"
    assert load_theme(cfg) == "dark"


def test_load_theme_returns_saved_value(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    assert load_theme(cfg) == "light"


def test_load_theme_returns_dark_on_corrupt_file(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("not json", encoding="utf-8")
    assert load_theme(cfg) == "dark"


def test_save_theme_writes_json(tmp_path):
    cfg = tmp_path / "config.json"
    save_theme("light", cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "light"


def test_save_theme_overwrites_existing(tmp_path):
    cfg = tmp_path / "config.json"
    save_theme("light", cfg)
    save_theme("dark", cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
