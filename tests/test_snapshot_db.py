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


def _login(db, character, username, sort=0):
    db.login_upsert(
        {
            "character": character,
            "username": username,
            "server": "s",
            "password": None,
            "protect": None,
            "enabled": 1,
            "sort": sort,
        }
    )


def test_sync_login_accounts_groups_a_shared_login(db):
    _login(db, "甲", "u1", 0)
    _login(db, "乙", "u1", 1)
    _login(db, "丙", "u2")  # alone on its login: no account needed
    assert db.sync_login_accounts() == 2
    a, b = db.get_character_account("甲"), db.get_character_account("乙")
    assert a == b and a["name"] == "甲 / 乙"
    assert db.get_character_account("丙") is None
    assert "u1" not in {r["name"] for r in db.list_accounts()}
    assert db.sync_login_accounts() == 0  # nothing left to do


def test_sync_login_accounts_joins_the_account_already_there(db):
    acct = db.create_account("我的帳號")
    db.set_character_account("甲", acct)
    _login(db, "甲", "u1")
    _login(db, "乙", "u1")
    db.sync_login_accounts()
    assert db.get_character_account("乙")["id"] == acct
    assert [r["name"] for r in db.list_accounts()] == ["我的帳號"]


def test_sync_login_accounts_leaves_split_accounts_alone(db):
    a1, a2 = db.create_account("A"), db.create_account("B")
    db.set_character_account("甲", a1)
    db.set_character_account("乙", a2)
    for c in ("甲", "乙", "丙"):
        _login(db, c, "u1")
    assert db.sync_login_accounts() == 0
    assert db.get_character_account("丙") is None


def test_sync_login_accounts_names_around_a_taken_name(db):
    db.create_account("甲 / 乙")
    _login(db, "甲", "u1", 0)
    _login(db, "乙", "u1", 1)
    db.sync_login_accounts()
    assert db.get_character_account("甲")["name"] == "甲 / 乙 (2)"


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
    _login(db, "乙", "u1")
    acct = db.create_account("A")
    db.set_character_account("丙", acct)
    rows = {r["character"]: r["account_name"] for r in db.account_characters()}
    assert rows == {"甲": None, "乙": None, "丙": "A"}


def test_latest_warehouse_is_the_accounts_newest(db):
    acct = db.create_account("A")
    db.set_character_account("華沁", acct)
    db.set_character_account("天外", acct)
    db.save_snapshot("華沁", "warehouse", [{"item_id": 1, "qty": 9}])
    db.save_snapshot("天外", "warehouse", [{"item_id": 1, "qty": 2}])
    db._con.execute("UPDATE snapshots SET scanned_at='2026-01-01T00:00:00' WHERE character='華沁'")
    got = db.latest_warehouse("華沁")
    assert got["character"] == "天外" and got["items"] == [{"item_id": 1, "qty": 2}]
    assert db.latest_warehouse("路人") is None


def test_latest_warehouse_without_account_is_its_own(db):
    db.save_snapshot("甲", "warehouse", [{"item_id": 1, "qty": 9}])
    db.save_snapshot("乙", "warehouse", [{"item_id": 2, "qty": 1}])
    assert db.latest_warehouse("甲")["character"] == "甲"
