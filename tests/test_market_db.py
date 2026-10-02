import pytest

from reader import StallItem
from services.market_db import MarketDB, classify_price, silver_value


def item(item_id, price, qty=1, plus=0, stats=(), inlays=()):
    return StallItem(item_id=item_id, price=price, qty=qty, plus=plus, stats=stats, inlays=inlays)


@pytest.fixture
def db(tmp_path):
    d = MarketDB(str(tmp_path / "market.db"), values={})  # no bait flags unless a test sets values
    yield d
    d.close()


@pytest.mark.parametrize(
    "price,expected",
    [
        (99_999_999, ("negotiate", 0)),
        (99_999_200, ("coin", 200)),
        (99_999_920, ("coin", 20)),
        (99_990_001, ("coin", 1)),
        (99_990_000, ("silver", 0)),  # tail 0: a real ~1 億 ask
        (99_980_000, ("silver", 0)),  # only three leading 9s
        (9_990_000, ("silver", 0)),  # 7 digits
        (88_888_888, ("silver", 0)),  # repdigits are real asks
        (7_777_777, ("silver", 0)),
        (2_000_000, ("silver", 0)),
    ],
)
def test_classify_price(price, expected):
    assert classify_price(price) == expected


def test_silver_value():
    assert silver_value(99_999_999) is None
    assert silver_value(99_999_200) == 200_000_000
    assert silver_value(15_000) == 15_000


def test_identical_lots_merge_into_one_listing(db):
    swords = [item(20100, 2_000_000)] * 6
    r = db.record(
        "夜漠雪", "夜漠雪的商店", 173, "成都市集", 0, (*swords, item(24004, 1_000, qty=20)), now=10
    )
    assert (r.new, r.unchanged) == (2, 0)
    rows = {row["item_id"]: row for row in r.rows}
    assert rows[20100]["count"] == 6
    assert rows[24004]["count"] == 20


def test_reopen_unchanged_only_moves_last_seen(db):
    stall = (item(20100, 2_000_000), item(24004, 1_000, qty=20))
    db.record("A", "", 173, "成都市集", 0, stall, now=10)
    r = db.record("A", "", 173, "成都市集", 0, stall, now=20)
    assert (r.new, r.unchanged, r.changed, r.gone) == (0, 2, [], [])
    listings = db.listings_for_item(20100)
    assert len(listings) == 1
    assert (listings[0]["first_seen"], listings[0]["last_seen"]) == (10, 20)
    assert db.totals()["visits"] == 2


def test_count_drop_and_gone(db):
    db.record(
        "A", "", 173, "成都市集", 0, (item(33620, 15_000, qty=160), item(20100, 2_000_000)), now=10
    )
    r = db.record("A", "", 173, "成都市集", 0, (item(33620, 15_000, qty=150),), now=20)
    assert r.changed == [(33620, 15_000, 160, 150)]
    assert r.gone == [(20100, 2_000_000)]
    assert db.listings_for_item(20100)[0]["ended_at"] == 20
    assert db.listings_for_item(20100, include_ended=False) == []


def test_price_change_is_a_new_listing(db):
    db.record("A", "", 173, "成都市集", 0, (item(20100, 2_000_000),), now=10)
    r = db.record("A", "", 173, "成都市集", 0, (item(20100, 1_800_000),), now=20)
    assert r.new == 1 and r.gone == [(20100, 2_000_000)]


def test_attributes_keep_copies_apart(db):
    a = item(21124, 500_000, stats=(("damage_min", 464),))
    b = item(21124, 500_000, stats=(("damage_min", 470),))
    r = db.record("A", "", 173, "成都市集", 0, (a, b), now=10)
    assert r.new == 2


def test_sellers_are_independent(db):
    db.record("A", "", 173, "成都市集", 0, (item(20100, 2_000_000),), now=10)
    db.record("B", "", 173, "成都市集", 0, (), now=20)
    assert db.listings_for_item(20100)[0]["ended_at"] is None


def test_item_summaries_skip_negotiate_in_stats(db):
    db.record(
        "A", "", 173, "成都市集", 0, (item(20100, 2_000_000), item(20100, 99_999_999)), now=10
    )
    db.record("B", "", 173, "成都市集", 0, (item(20100, 99_999_200),), now=11)
    (s,) = db.item_summaries()
    assert (s["listings"], s["sellers"], s["negotiate"]) == (3, 2, 1)
    assert (s["min"], s["max"]) == (2_000_000, 200_000_000)
    (s,) = db.item_summaries(include_negotiate=False)
    assert s["listings"] == 2


def test_last_recorded(db):
    db.record("A", "", 173, "成都市集", 0, (), now=10)
    db.record("A", "", 173, "成都市集", 0, (), now=30)
    assert db.last_recorded() == {"A": 30}


def test_bait_price_is_flagged_and_left_out_of_stats(tmp_path):
    db = MarketDB(str(tmp_path / "b.db"), values={50938: 206_000, 24007: 250})
    db.record("sfewf", "鴿", 173, "成都市集", 0, (item(50938, 300),), now=10)
    db.record("A", "", 173, "成都市集", 0, (item(50938, 180_000), item(24007, 140)), now=11)
    by_item = {s["item_id"]: s for s in db.item_summaries()}
    doll = by_item[50938]
    assert doll["flagged"] == 1 and doll["min"] == 180_000
    assert by_item[24007]["flagged"] == 0  # 140 vs value 250: a real cheap sale
    rows = {r["seller"]: r for r in db.listings_for_item(50938)}
    assert rows["sfewf"]["suspect"] and not rows["A"]["suspect"]
    db.close()


def test_manual_exclusions(db):
    db.record("A", "", 173, "成都市集", 0, (item(20100, 2_000_000), item(20101, 5_000)), now=10)
    db.record("B", "", 173, "成都市集", 0, (item(20100, 1_000_000),), now=11)
    b_listing = next(r for r in db.listings_for_item(20100) if r["seller"] == "B")
    assert db.set_listing_excluded(b_listing["id"], True)
    (s,) = [s for s in db.item_summaries() if s["item_id"] == 20100]
    assert (s["min"], s["flagged"]) == (2_000_000, 1)
    db.set_seller_excluded("A", True)
    excluded = {r["seller"]: r["excluded"] for r in db.listings_for_item(20100)}
    assert excluded == {"A": "seller", "B": "listing"}
    (s,) = [s for s in db.item_summaries() if s["item_id"] == 20100]
    assert s["min"] is None
    db.set_seller_excluded("A", False)
    db.set_listing_excluded(b_listing["id"], False)
    assert all(r["excluded"] is None for r in db.listings_for_item(20100))
    assert db.set_listing_excluded(99999, True) is False
