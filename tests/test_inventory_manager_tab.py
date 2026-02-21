"""Tests for InventoryManagerTab By Char card list view."""

import pytest
from gui.inventory_manager_tab import InventoryManagerTab, _MODE_BY_CHAR
from gui.snapshot_db import SnapshotDB


@pytest.fixture
def db(tmp_path):
    instance = SnapshotDB(str(tmp_path / "test.db"))
    yield instance
    instance.close()


def test_tab_by_char_uses_card_list(db, qtbot):
    db.save_snapshot("Alice", "inventory", [{"item_id": 1, "qty": 1}])
    tab = InventoryManagerTab(db)
    qtbot.addWidget(tab)
    tab._set_mode(_MODE_BY_CHAR)
    tab.refresh()
    assert hasattr(tab, "_cards")
    assert "Alice" in tab._cards


def test_tab_by_char_has_no_flat_table(db, qtbot):
    tab = InventoryManagerTab(db)
    qtbot.addWidget(tab)
    # The old flat QTableWidget for By Char should not exist
    assert not hasattr(tab, "_table")
