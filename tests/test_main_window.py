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
