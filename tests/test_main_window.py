"""Tests for MainWindow: DataManagementTab wired as page index 2."""

import pytest
from gui.main_window import MainWindow


@pytest.fixture
def main_window(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    return win


def test_main_window_has_data_mgmt_button(main_window):
    assert hasattr(main_window, "_btn_data_mgmt")
    assert main_window._btn_data_mgmt is not None


def test_switch_to_data_management_page(main_window):
    main_window._switch_page(2)
    assert main_window._stack.currentIndex() == 2


def test_switch_back_to_inventory_page(main_window):
    main_window._switch_page(2)
    main_window._switch_page(1)
    assert main_window._stack.currentIndex() == 1


def test_main_window_has_theme_toggle_button(main_window):
    assert hasattr(main_window, "_btn_theme")
    assert main_window._btn_theme is not None


def test_theme_toggle_button_label_when_dark(main_window):
    from gui.theme import ThemeManager, DARK_PALETTE

    ThemeManager._mode = "dark"
    ThemeManager._palette = DARK_PALETTE
    main_window._update_theme_btn_label()
    assert "亮色" in main_window._btn_theme.text()


def test_theme_toggle_button_label_when_light(main_window):
    from gui.theme import ThemeManager, LIGHT_PALETTE

    ThemeManager._mode = "light"
    ThemeManager._palette = LIGHT_PALETTE
    main_window._update_theme_btn_label()
    assert "暗色" in main_window._btn_theme.text()
