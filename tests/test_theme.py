"""Tests for ThemeManager and palette system."""

import pytest
from unittest.mock import patch
from gui.theme import ThemeManager, DARK_PALETTE


@pytest.fixture(autouse=True)
def reset_theme_manager():
    """Reset ThemeManager to dark defaults before each test."""
    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    ThemeManager._app = None
    yield
    # Reset after test too
    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    ThemeManager._app = None


def test_theme_manager_default_mode():
    from gui.theme import ThemeManager, DARK_PALETTE

    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    assert ThemeManager.mode() == "dark"


def test_theme_manager_c_returns_dark_green():
    from gui.theme import ThemeManager, DARK_PALETTE

    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    assert ThemeManager.c("GREEN") == "#22C55E"


def test_theme_manager_c_returns_light_bg():
    from gui.theme import ThemeManager, LIGHT_PALETTE

    ThemeManager._mode = "light"
    ThemeManager._palette = LIGHT_PALETTE
    assert ThemeManager.c("BG_BASE") == "#F8FAFC"


def test_theme_manager_c_dark_bg():
    from gui.theme import ThemeManager, DARK_PALETTE

    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    assert ThemeManager.c("BG_BASE") == "#020617"


def test_badge_style_returns_string():
    from gui.theme import ThemeManager, DARK_PALETTE, badge_style

    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    result = badge_style("LOCATED")
    assert "color" in result
    assert "#" in result


def test_vital_html_contains_key():
    from gui.theme import ThemeManager, DARK_PALETTE, vital_html

    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    result = vital_html("HP", 100)
    assert "HP" in result


def test_theme_manager_toggle_dark_to_light():
    from gui.theme import ThemeManager, DARK_PALETTE, LIGHT_PALETTE

    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    ThemeManager._app = None
    with patch("gui.theme.save_theme"):
        ThemeManager.toggle()
    assert ThemeManager._mode == "light"
    assert ThemeManager._palette is LIGHT_PALETTE


def test_theme_manager_toggle_light_to_dark():
    from gui.theme import ThemeManager, DARK_PALETTE, LIGHT_PALETTE

    ThemeManager._mode = "light"
    ThemeManager._palette = LIGHT_PALETTE
    ThemeManager._app = None
    with patch("gui.theme.save_theme"):
        ThemeManager.toggle()
    assert ThemeManager._mode == "dark"
    assert ThemeManager._palette is DARK_PALETTE


def test_build_qss_contains_bg_base():
    from gui.theme import _build_qss, DARK_PALETTE, LIGHT_PALETTE

    dark_qss = _build_qss(DARK_PALETTE)
    light_qss = _build_qss(LIGHT_PALETTE)
    assert "#020617" in dark_qss  # dark BG_BASE
    assert "#F8FAFC" in light_qss  # light BG_BASE
    assert "#020617" not in light_qss  # dark color not in light QSS


def test_dark_qss_alias_exists():
    from gui.theme import DARK_QSS

    assert isinstance(DARK_QSS, str)
    assert len(DARK_QSS) > 100


def test_badge_style_light_mode_returns_different_colors():
    from gui.theme import ThemeManager, LIGHT_PALETTE, badge_style

    ThemeManager._mode = "light"
    ThemeManager._palette = LIGHT_PALETTE
    dark_result = badge_style("LOCATED")  # but using light mode
    # Light mode LOCATED badge should have light green bg (DCFCE7), not dark (#0D2417)
    assert "#DCFCE7" in badge_style("LOCATED")
    assert "#0D2417" not in badge_style("LOCATED")
