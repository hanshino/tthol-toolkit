import pytest

import reader
from reader import Staller, StallItem
from services import market_survey
from services.market_db import MarketDB
from services.market_survey import MarketSurveyManager, StallTracker


def item(item_id, price=1_000, qty=1):
    return StallItem(item_id=item_id, price=price, qty=qty, plus=0, stats=(), inlays=())


A = (item(1), item(2))
B = (item(3),)


def feed(tracker, t, wnd, seller, content, polls=4, dt=0.1):
    """Observe the same state `polls` times, recording each settled read like the
    manager does; return all events and the next time."""
    events = []
    for _ in range(polls):
        new = tracker.observe(t, wnd, seller, content)
        for kind, payload in new:
            if kind == "settled":
                tracker.mark_recorded(payload)
        events += new
        t += dt
    return events, t


def settled(events):
    return [p for k, p in events if k == "settled"]


def test_new_stall_settles_after_stable_reads():
    tr = StallTracker()
    tr.observe(0, None, None, B)  # baseline: last stall viewed was B
    ev, _ = feed(tr, 1, 100, "A", A)
    (s,) = settled(ev)
    assert (s.seller, s.items) == ("A", A)
    assert ev[0] == ("opened", "A")


def test_settles_once_per_window():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, t = feed(tr, 1, 100, "A", A)
    ev, _ = feed(tr, t, 100, "A", A, polls=10)
    assert settled(ev) == []


def test_content_change_while_open_is_recorded_again():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, t = feed(tr, 1, 100, "A", A)
    ev, t = feed(tr, t, 100, "A", (item(1),), polls=4)
    assert settled(ev) == []  # waits for a lagging label first
    ev, _ = feed(tr, t, 100, "A", (item(1),), polls=8)
    assert len(settled(ev)) == 1


def test_in_place_switch_with_lagging_label():
    tr = StallTracker()
    tr.observe(0, None, None, (item(9),))
    ev, t = feed(tr, 1, 100, "A", A)
    assert settled(ev)[0].seller == "A"
    # B's items land while the dialog still says A ...
    ev, t = feed(tr, t, 100, "A", B, polls=5)
    assert settled(ev) == []
    # ... then the label catches up: B's items go to B.
    ev, _ = feed(tr, t, 100, "B", B)
    (s,) = settled(ev)
    assert (s.seller, s.items) == ("B", B)


def test_reopen_same_stall_settles_on_old_buffer():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, t = feed(tr, 1, 100, "A", A)
    tr.observe(t, None, None, A)  # closed
    ev, _ = feed(tr, t + 1, 101, "A", A)
    assert len(settled(ev)) == 1


def test_previous_sellers_buffer_is_not_attributed_to_next_seller():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, t = feed(tr, 1, 100, "A", A)
    tr.observe(t, None, None, A)  # close A
    # B's window shows, but the buffer still holds A's items
    ev, t = feed(tr, t + 1, 101, "B", A, polls=5)
    assert settled(ev) == []
    ev, _ = feed(tr, t, 101, "B", B)  # B's data arrives
    (s,) = settled(ev)
    assert (s.seller, s.items) == ("B", B)


def test_other_sellers_buffer_times_out_as_stale():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, t = feed(tr, 1, 100, "A", A)
    tr.observe(t, None, None, A)  # close A
    ev, _ = feed(tr, t + 1, 101, "B", A, polls=40)  # B's data never arrives
    assert settled(ev) == []
    assert ("stale", "B") in ev


def test_unowned_buffer_settles_after_timeout():
    tr = StallTracker()
    tr.observe(0, None, None, A)  # buffer predates recording: nobody owns it
    ev, t = feed(tr, 1, 100, "A", A, polls=20)
    assert settled(ev) == []
    ev, _ = feed(tr, t, 100, "A", A, polls=20)
    assert settled(ev)[0].seller == "A"


def test_empty_read_is_never_recorded():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, _ = feed(tr, 1, 100, "A", (), polls=40)
    assert settled(ev) == []


def test_npc_shop_without_seller_label():
    tr = StallTracker()
    tr.observe(0, None, None, A)
    ev, _ = feed(tr, 1, 100, None, A, polls=20)
    assert ("npc", None) in ev
    assert settled(ev) == []


def test_late_seller_label():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, t = feed(tr, 1, 100, None, A, polls=3)
    assert settled(ev) == []
    ev, _ = feed(tr, t, 100, "A", A)
    assert settled(ev)[0].seller == "A"


def test_switching_stalls_in_place_reopens():
    tr = StallTracker()
    tr.observe(0, None, None, B)
    ev, t = feed(tr, 1, 100, "A", A)
    ev, _ = feed(tr, t, 100, "B", B)  # same window, label changed
    assert ("opened", "B") in ev
    assert settled(ev)[0].seller == "B"


# -- manager with a faked reader ---------------------------------------


class FakeGame:
    def __init__(self):
        self.stage = (173, "成都市集")
        self.wnd = None
        self.labels = []
        self.content = B
        self.stallers = {"A": Staller("A的商店", 20, 26), "B": Staller("清倉")}
        self.scans = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(reader, "read_stage", lambda pm: self.stage)
        monkeypatch.setattr(reader, "_window_list", lambda pm: [self.wnd] if self.wnd else [])
        monkeypatch.setattr(reader, "find_shop_window", lambda pm, ws: self.wnd)
        monkeypatch.setattr(reader, "read_shop_labels", lambda pm, ws, w: list(self.labels))
        monkeypatch.setattr(reader, "read_viewed_stall", lambda pm, hp: self.content)

        def scan(pm, hp_addr=None):
            self.scans += 1
            return dict(self.stallers)

        monkeypatch.setattr(reader, "scan_stallers", scan)


@pytest.fixture
def game(monkeypatch):
    g = FakeGame()
    g.install(monkeypatch)
    return g


@pytest.fixture
def mgr(tmp_path):
    db = MarketDB(str(tmp_path / "m.db"))
    clock = {"t": 1000.0}

    def now():
        clock["t"] += market_survey.POLL_S
        return clock["t"]

    m = MarketSurveyManager(live=lambda pid: ("pm", 0x1000), pids=lambda: [1], db=db, clock=now)
    yield m
    db.close()


def run(mgr, n=6):
    for _ in range(n):
        mgr.step(1)


def test_manager_records_an_opened_stall(game, mgr):
    run(mgr, 2)
    game.wnd, game.labels, game.content = 0x500, ["數量", "A", "商店"], A
    run(mgr)
    st = mgr.status(1)
    assert st["active"] and st["reason"] == "recording"
    assert st["current"]["seller"] == "A" and st["current"]["new"] == 2
    assert (st["current"]["sign"], st["current"]["x"], st["current"]["y"]) == ("A的商店", 20, 26)
    listing = mgr._db.listings_for_item(1)[0]
    assert (listing["sign"], listing["x"], listing["y"]) == ("A的商店", 20, 26)
    assert st["session"] == {"stalls": 1, "new": 2, "reads": 1}
    assert {(s["seller"], s["x"]) for s in st["stalls"]} == {("A", 20), ("B", None)}


def test_manager_pauses_off_market(game, mgr):
    game.stage = (1, "某城")
    run(mgr, 2)
    st = mgr.status(1)
    assert not st["active"] and st["reason"] == "not_market"
    mgr.set_mode(1, "on")
    run(mgr, 1)
    assert mgr.status(1)["active"]


def test_manager_off_mode(game, mgr):
    mgr.set_mode(1, "off")
    run(mgr, 2)
    assert mgr.status(1)["reason"] == "off"


def test_manager_rescans_for_unknown_seller(game, mgr):
    run(mgr, 2)
    scans = game.scans
    game.stallers["C"] = Staller("新來的")
    game.wnd, game.labels, game.content = 0x500, ["C"], (item(9),)
    run(mgr)
    assert game.scans == scans + 1
    assert mgr.status(1)["current"]["seller"] == "C"


def test_mode_survives_a_pid_gap(game, tmp_path):
    db = MarketDB(str(tmp_path / "m.db"))
    pids = {"live": [1]}
    clock = {"t": 0.0}
    m = MarketSurveyManager(
        live=lambda pid: ("pm", 0x1000), pids=lambda: pids["live"], db=db, clock=lambda: clock["t"]
    )
    m.set_mode(1, "off")
    pids["live"] = []  # 重偵 pops the session for a moment
    m._forget_gone(set())
    clock["t"] = 5.0
    m._forget_gone(set())
    pids["live"] = [1]
    m.step(1)
    assert m.status(1)["mode"] == "off" and m.status(1)["reason"] == "off"
    db.close()


def test_scan_time_does_not_count_toward_timeouts(game, mgr, monkeypatch):
    run(mgr, 2)
    game.stallers["C"] = Staller("新來的")
    game.wnd, game.labels, game.content = 0x500, ["C"], (item(9),)
    slow = game.scans

    def slow_scan(pm, hp_addr=None):
        game.scans += 1
        for _ in range(30):  # ~3 s of clock per scan
            mgr._clock()
        return dict(game.stallers)

    monkeypatch.setattr(reader, "scan_stallers", slow_scan)
    run(mgr)
    assert game.scans == slow + 1
    assert mgr.status(1)["current"]["seller"] == "C"


def test_manager_without_character(game, tmp_path):
    db = MarketDB(str(tmp_path / "m.db"))
    m = MarketSurveyManager(live=lambda pid: None, pids=lambda: [1], db=db)
    m.step(1)
    assert m.status(1)["reason"] == "no_character"
    db.close()
