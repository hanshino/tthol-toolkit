"""Tests for CharacterCard widget."""

import pytest
from gui.character_card import CharacterCard
from gui.snapshot_db import SnapshotDB


@pytest.fixture
def db(tmp_path):
    instance = SnapshotDB(str(tmp_path / "test.db"))
    yield instance
    instance.close()


def test_card_instantiates(db, qtbot):
    rows = [
        {
            "character": "Alice",
            "source": "inventory",
            "item_id": 1,
            "qty": 5,
            "name": "Sword",
            "scanned_at": "2026-01-01T10:00:00",
            "account": None,
        }
    ]
    card = CharacterCard("Alice", rows, db)
    qtbot.addWidget(card)
    assert card is not None


def test_card_has_no_manage_button(db, qtbot):
    rows = [
        {
            "character": "Alice",
            "source": "inventory",
            "item_id": 1,
            "qty": 5,
            "name": "Sword",
            "scanned_at": "2026-01-01T10:00:00",
            "account": None,
        }
    ]
    card = CharacterCard("Alice", rows, db)
    qtbot.addWidget(card)
    assert not hasattr(card, "_manage_btn")
    assert not hasattr(card, "_panel")


def test_card_shows_character_name(db, qtbot):
    rows = [
        {
            "character": "Alice",
            "source": "inventory",
            "item_id": 1,
            "qty": 5,
            "name": "Sword",
            "scanned_at": "2026-01-01T10:00:00",
            "account": None,
        }
    ]
    card = CharacterCard("Alice", rows, db)
    qtbot.addWidget(card)
    assert "Alice" in card._name_lbl.text()


def test_card_table_has_rows(db, qtbot):
    rows = [
        {
            "character": "Alice",
            "source": "inventory",
            "item_id": 1,
            "qty": 5,
            "name": "Sword",
            "scanned_at": "2026-01-01T10:00:00",
            "account": None,
        },
        {
            "character": "Alice",
            "source": "inventory",
            "item_id": 2,
            "qty": 3,
            "name": "Shield",
            "scanned_at": "2026-01-01T10:00:00",
            "account": None,
        },
    ]
    card = CharacterCard("Alice", rows, db)
    qtbot.addWidget(card)
    assert card._table.rowCount() == 2
