"""Tests for gui.config — theme preference persistence."""

import json
import pytest
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


def test_save_theme_updates_value_on_second_call(tmp_path):
    cfg = tmp_path / "config.json"
    save_theme("light", cfg)
    save_theme("dark", cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"


def test_save_theme_succeeds_when_existing_file_corrupt(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("not json", encoding="utf-8")
    save_theme("light", cfg)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "light"


def test_save_theme_raises_on_invalid_mode(tmp_path):
    cfg = tmp_path / "config.json"
    with pytest.raises(ValueError, match="Invalid theme"):
        save_theme("invalid", cfg)
