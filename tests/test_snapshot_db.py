import pytest
from services.snapshot_db import SnapshotDB


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


def test_rename_and_delete_account(db):
    a1, a2 = db.create_account("A"), db.create_account("B")
    assert db.rename_account(a1, "C")
    assert not db.rename_account(a1, "B")
    db.set_character_account("甲", a2)
    assert not db.delete_account(a2)
    assert db.delete_account(a1)
    assert [r["name"] for r in db.list_accounts()] == ["B"]


def test_account_characters_lists_every_known_character(db):
    db.save_snapshot("甲", "inventory", [{"item_id": 1, "qty": 1}])
    acct = db.create_account("A")
    db.set_character_account("丙", acct)
    rows = {r["character"]: r["account_name"] for r in db.account_characters()}
    assert rows == {"甲": None, "丙": "A"}


def test_latest_warehouse_is_the_accounts_newest(db):
    acct = db.create_account("A")
    db.set_character_account("測試癸", acct)
    db.set_character_account("天外", acct)
    db.save_snapshot("測試癸", "warehouse", [{"item_id": 1, "qty": 9}])
    db.save_snapshot("天外", "warehouse", [{"item_id": 1, "qty": 2}])
    db._con.execute(
        "UPDATE snapshots SET scanned_at='2026-01-01T00:00:00' WHERE character='測試癸'"
    )
    got = db.latest_warehouse("測試癸")
    assert got["character"] == "天外" and got["items"] == [{"item_id": 1, "qty": 2}]
    assert db.latest_warehouse("路人") is None


def test_latest_warehouse_without_account_is_its_own(db):
    db.save_snapshot("甲", "warehouse", [{"item_id": 1, "qty": 9}])
    db.save_snapshot("乙", "warehouse", [{"item_id": 2, "qty": 1}])
    assert db.latest_warehouse("甲")["character"] == "甲"
