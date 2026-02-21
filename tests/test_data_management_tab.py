"""Tests for DataManagementTab."""

import pytest
from gui.data_management_tab import DataManagementTab
from gui.snapshot_db import SnapshotDB


@pytest.fixture
def db(tmp_path):
    instance = SnapshotDB(str(tmp_path / "test.db"))
    yield instance
    instance.close()


def test_tab_instantiates(db, qtbot):
    tab = DataManagementTab(db)
    qtbot.addWidget(tab)
    assert tab is not None


def test_tab_shows_placeholder_when_no_char_selected(db, qtbot):
    tab = DataManagementTab(db)
    qtbot.addWidget(tab)
    # right stack index 0 = placeholder
    assert tab._right_stack.currentIndex() == 0


def test_tab_populates_character_list(db, qtbot):
    db.save_snapshot("Alice", "inventory", [{"item_id": 1, "qty": 1}])
    tab = DataManagementTab(db)
    qtbot.addWidget(tab)
    tab.refresh()
    assert tab._char_list.count() == 1
    item = tab._char_list.item(0)
    assert "Alice" in item.text()


def test_tab_shows_detail_on_char_selected(db, qtbot):
    db.save_snapshot("Alice", "inventory", [{"item_id": 1, "qty": 1}])
    tab = DataManagementTab(db)
    qtbot.addWidget(tab)
    tab.refresh()
    tab._char_list.setCurrentRow(0)
    assert tab._right_stack.currentIndex() == 1


def test_tab_reselects_character_after_refresh(db, qtbot):
    db.save_snapshot("Alice", "inventory", [{"item_id": 1, "qty": 1}])
    tab = DataManagementTab(db)
    qtbot.addWidget(tab)
    tab.refresh()
    tab._char_list.setCurrentRow(0)
    tab.refresh()
    assert tab._selected_character == "Alice"
