import pytest
from gui.snapshot_db import SnapshotDB


@pytest.fixture
def db(tmp_path):
    instance = SnapshotDB(str(tmp_path / "test.db"))
    yield instance
    instance.close()


def test_save_and_load(db):
    items = [{"item_id": 100, "qty": 3}, {"item_id": 200, "qty": 1}]
    saved = db.save_snapshot("Hero", "inventory", items)
    assert saved is True
    rows = db.load_latest_snapshots()
    assert len(rows) == 2
    assert rows[0]["character"] == "Hero"
    assert rows[0]["source"] == "inventory"
    assert {r["item_id"] for r in rows} == {100, 200}


def test_dedup_skips_identical(db):
    items = [{"item_id": 100, "qty": 3}]
    db.save_snapshot("Hero", "inventory", items)
    saved = db.save_snapshot("Hero", "inventory", items)
    assert saved is False


def test_dedup_saves_when_changed(db):
    db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 3}])
    saved = db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 5}])
    assert saved is True


def test_load_returns_only_latest_per_character_source(db):
    db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 1}])
    db.save_snapshot("Hero", "inventory", [{"item_id": 100, "qty": 2}])
    rows = db.load_latest_snapshots()
    assert len(rows) == 1
    assert rows[0]["qty"] == 2


def test_multiple_characters(db):
    db.save_snapshot("Hero", "inventory", [{"item_id": 1, "qty": 1}])
    db.save_snapshot("Alt", "inventory", [{"item_id": 2, "qty": 5}])
    rows = db.load_latest_snapshots()
    chars = {r["character"] for r in rows}
    assert chars == {"Hero", "Alt"}


def test_list_characters_returns_all(db):
    db.save_snapshot("Alice", "inventory", [{"item_id": 1, "qty": 1}])
    db.save_snapshot("Bob", "warehouse", [{"item_id": 2, "qty": 2}])
    chars = db.list_characters()
    names = [c["character"] for c in chars]
    assert sorted(names) == ["Alice", "Bob"]


def test_list_characters_includes_account(db):
    db.save_snapshot("Alice", "inventory", [{"item_id": 1, "qty": 1}])
    acct_id = db.create_account("TestAccount")
    db.set_character_account("Alice", acct_id)
    chars = db.list_characters()
    alice = next(c for c in chars if c["character"] == "Alice")
    assert alice["account_name"] == "TestAccount"
    assert alice["account_id"] == acct_id


def test_list_characters_no_account_returns_none(db):
    db.save_snapshot("Bob", "inventory", [{"item_id": 1, "qty": 1}])
    chars = db.list_characters()
    bob = next(c for c in chars if c["character"] == "Bob")
    assert bob["account_name"] is None
    assert bob["account_id"] is None


def test_list_characters_empty_when_no_snapshots(db):
    chars = db.list_characters()
    assert chars == []
