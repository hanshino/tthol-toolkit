"""Fast position stream: the worker's between-poll tile sampling, the session
seq, the manager's batched frames and /ws/pos."""

import asyncio
import struct

import pytest
from fastapi.testclient import TestClient

import services.worker as W
from services.api import build_app
from services.api_types import Position, PositionFrame
from services.char_session import CharSession
from services.worker import ReaderWorker
from services.worker_manager import WorkerManager

HP = 0x1000_0000
FREED = W.FREED_FILL


class FakePM:
    def __init__(self, x: int, y: int) -> None:
        self.x, self.y = x, y

    def read_bytes(self, addr: int, n: int) -> bytes:
        assert (addr, n) == (HP + 416, 8)
        return struct.pack("<ii", self.x, self.y)


@pytest.fixture
def worker(monkeypatch):
    sent: list[tuple] = []
    w = ReaderWorker(
        pid=1,
        on_state=lambda _s: None,
        on_stats=lambda _s: None,
        on_inventory=lambda _i: None,
        on_warehouse=lambda _w: None,
        on_error=lambda *_a, **_k: None,
        on_position=lambda *a: sent.append(a),
    )
    w._stage_names = {"成都少城", "雲夢湖東"}
    w._stage_by_id = {53: "成都少城", 21: "雲夢湖東"}
    w.sent = sent
    w.stage = (53, "成都少城")
    w.score = 1.0
    monkeypatch.setattr(W, "read_stage", lambda _pm: w.stage)
    monkeypatch.setattr(W, "verify_structure", lambda *_a: w.score)
    monkeypatch.setattr(W, "minimap_base", lambda _sid: {"w_tiles": 200, "h_tiles": 200})
    return w


def _first_sample(worker, pm):
    """The first sample is sent as is: there is no earlier map to hold back."""
    assert worker._sample_position(pm, HP) is False
    assert worker.sent == [(53, "成都少城", pm.x, pm.y)]
    worker.sent.clear()


def test_changed_position_is_sent_once(worker):
    pm = FakePM(67, 142)
    _first_sample(worker, pm)
    pm.x = 66
    worker._sample_position(pm, HP)
    worker._sample_position(pm, HP)
    assert worker.sent == [(53, "成都少城", 66, 142)]


def test_failed_verify_is_dropped_but_not_a_loss(worker):
    pm = FakePM(67, 142)
    _first_sample(worker, pm)
    pm.x, worker.score = 66, 0.5
    assert worker._sample_position(pm, HP) is False
    assert worker.sent == []


def test_freed_struct_reports_a_loss(worker):
    pm = FakePM(67, 142)
    _first_sample(worker, pm)
    pm.x = pm.y = FREED
    worker.score = 0.0
    assert worker._sample_position(pm, HP) is True


def test_out_of_bounds_tile_is_dropped(worker):
    pm = FakePM(67, 142)
    _first_sample(worker, pm)
    pm.x = 250
    worker._sample_position(pm, HP)
    assert worker.sent == []


def test_stage_change_hides_the_dot_until_the_tiles_move(worker):
    pm = FakePM(67, 142)
    _first_sample(worker, pm)
    pm.x = 66
    worker._sample_position(pm, HP)
    worker.sent.clear()
    worker.stage = (21, "雲夢湖東")
    worker._sample_position(pm, HP)  # tiles still the old map's (66, 142)
    worker._sample_position(pm, HP)
    assert worker.sent == [(21, "雲夢湖東", -1, -1)]
    pm.x, pm.y = 124, 8
    worker._sample_position(pm, HP)
    assert worker.sent[-1] == (21, "雲夢湖東", 124, 8)


def test_unknown_stage_name_keeps_the_last_trusted_stage(worker):
    pm = FakePM(67, 142)
    _first_sample(worker, pm)
    worker.stage = (999, "garbage")
    pm.x = 66
    worker._sample_position(pm, HP)
    assert worker.sent == [(53, "成都少城", 66, 142)]


def test_torn_stage_read_is_not_trusted(worker):
    # Seen live mid map change: the old id with the new map's name.
    pm = FakePM(67, 142)
    _first_sample(worker, pm)
    worker.stage = (53, "雲夢湖東")
    pm.x = 66
    worker._sample_position(pm, HP)
    assert worker.sent == [(53, "成都少城", 66, 142)]


def test_track_position_returns_on_wake(worker):
    pm = FakePM(67, 142)
    worker._wake_event.set()
    assert worker._track_position(pm, HP, deadline=float("inf")) is False


def test_track_position_reports_a_freed_struct(worker):
    pm = FakePM(FREED, FREED)
    worker.score = 0.0
    assert worker._track_position(pm, HP, deadline=float("inf")) is True


def test_quick_relocate_shortens_only_the_first_waits(worker, monkeypatch):
    waits: list[float] = []

    class StopEvent:
        def is_set(self):
            return False

        def wait(self, t):
            waits.append(t)

    monkeypatch.setattr(W, "LOCATE_MAX_RETRIES", 6)
    worker._stop_event = StopEvent()
    worker._locate = lambda _pm, silent=False: None
    worker._report_locate_exhausted = lambda _pm: None
    assert worker._locate_with_retries(object(), "RESCANNING", quick=True) is None
    assert waits == [W.QUICK_RETRY_INTERVAL] * W.QUICK_RETRIES + [W.LOCATE_RETRY_INTERVAL] * 3


def test_session_position_feeds_the_row():
    sess = CharSession(1)
    sess._on_stats([("角色名稱", "某人"), ("X座標", 1), ("Y座標", 1)])
    assert sess.position()[0] == 0
    sess._on_position(53, "成都少城", 66, 142)
    seq, pos = sess.position()
    assert seq == 1
    assert (pos.stage_id, pos.map_name, pos.x, pos.y, pos.px) == (53, "成都少城", 66, 142, 2660)
    assert sess.row().position == pos


class FakeSession:
    def __init__(self, x: int = 0, seq: int = 1) -> None:
        self.seq, self.x = seq, x

    def position(self):
        return self.seq, Position(stage_id=53, x=self.x, y=1)


def test_position_frame_batches_changes_and_stays_quiet_when_idle():
    wm = WorkerManager()
    a, b = FakeSession(1), FakeSession(2)
    wm._sessions = {10: a, 20: b, 30: FakeSession(seq=0)}
    sent: dict = {}
    frame = wm.position_frame(sent)
    assert set(frame.pos) == {10, 20}  # 30 has no fast sample yet
    assert wm.position_frame(sent) is None
    a.seq, a.x = 2, 5
    assert wm.position_frame(sent).pos == {10: Position(stage_id=53, x=5, y=1)}
    del wm._sessions[20]
    wm.position_frame(sent)
    assert 20 not in sent


def test_position_frame_picks_up_a_rebuilt_session():
    wm = WorkerManager()
    wm._sessions = {10: FakeSession(seq=3)}
    sent: dict = {}
    wm.position_frame(sent)
    wm._sessions = {10: FakeSession(x=9, seq=3)}  # 重偵: new session, same seq
    assert wm.position_frame(sent).pos[10].x == 9


def test_position_frame_without_sent_is_a_full_frame():
    wm = WorkerManager()
    wm._sessions = {10: FakeSession()}
    assert set(wm.position_frame().pos) == {10}


def test_position_ws_receives_frames():
    app = build_app(services=None)
    stream = app.state.services["position_stream"]

    async def push():
        await asyncio.sleep(0.05)
        await stream.publish(PositionFrame(pos={7: Position(x=3, y=4)}))

    client = TestClient(app)
    with client.websocket_connect("/ws/pos") as ws:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(push())
        msg = ws.receive_json()
        assert msg["pos"]["7"]["x"] == 3


# --------------------------------------------------------------------------
# Lock must sit inside a CCharObject (2026-10-01: a second client locked onto
# a look-alike block that verify_structure scored 1.0).
# --------------------------------------------------------------------------


def test_score_drops_when_a_char_object_lock_stops_being_one(worker, monkeypatch):
    worker._lock_is_obj = True
    monkeypatch.setattr(W, "is_char_object", lambda _pm, _a: False)
    assert worker._score(object(), HP) == 0.0
    monkeypatch.setattr(W, "is_char_object", lambda _pm, _a: True)
    assert worker._score(object(), HP) == 1.0


def test_provisional_lock_moves_to_a_char_object(worker, monkeypatch):
    real = HP + 0x100
    monkeypatch.setattr(W, "is_char_object", lambda _pm, a: a == real)
    worker._locate = lambda _pm, silent=False: real
    assert worker._find_char_object(object(), HP) == real
    assert worker._lock_is_obj is True


def test_provisional_lock_stays_when_no_char_object_exists(worker, monkeypatch):
    monkeypatch.setattr(W, "is_char_object", lambda _pm, _a: False)
    worker._locate = lambda _pm, silent=False: HP + 0x100
    assert worker._find_char_object(object(), HP) is None
    assert worker._lock_is_obj is False


def test_locate_character_prefers_a_char_object(monkeypatch):
    import reader

    fake, real = 0x1B13D804, 0x1B13F000
    hp = struct.pack("<i", 907)

    class PM:
        process_handle = None

        def read_bytes(self, base, size):
            buf = bytearray(size)
            for a in (fake, real):
                buf[a - base : a - base + 4] = hp
            return bytes(buf)

    monkeypatch.setattr(reader, "get_memory_regions", lambda _h: [(0x1B13D000, 0x3000)])
    monkeypatch.setattr(reader, "verify_structure", lambda _pm, _a, _f: 1.0)
    monkeypatch.setattr(reader, "is_char_object", lambda _pm, a: a == real)
    kn = {"character_structure": {"fields": {}}}
    assert reader.locate_character(PM(), 907, kn) == real


def test_provisional_lock_publishes_no_name(worker, monkeypatch):
    # ";w9w" was read from the look-alike block after a character switch
    # (live 2026-10-06/07) and reported as a switch to that "character".
    monkeypatch.setattr(W, "read_character_name", lambda _pm, _a: ";w9w")
    worker._lock_is_obj = False
    assert worker._lock_name(object(), HP) == ""
    worker._lock_is_obj = True
    assert worker._lock_name(object(), HP) == ";w9w"


def test_provisional_recheck_runs_every_poll_at_first():
    fast = range(1, W.PROVISIONAL_FAST_POLLS + 1)
    assert all(W._provisional_recheck_due(t) for t in fast)
    later = range(W.PROVISIONAL_FAST_POLLS + 1, 40)
    due = [t for t in later if W._provisional_recheck_due(t)]
    assert due == [t for t in later if t % W.PROVISIONAL_RECHECK_EVERY == 0]


def test_pre_game_screens_wait_without_spending_retries(worker, monkeypatch):
    # The login screen used to burn the 10 retries in ~33 s and stop the
    # worker, so every login needed 重偵.
    screens = iter(["login"] * 30 + ["select"] * 5 + ["game"])
    waits: list[float] = []

    class StopEvent:
        def is_set(self):
            return False

        def wait(self, t):
            waits.append(t)
            return False

    monkeypatch.setattr(W, "screen_kind", lambda _pm: next(screens))
    monkeypatch.setattr(W, "is_char_object", lambda _pm, _a: True)
    worker._stop_event = StopEvent()
    located = []
    worker._locate = lambda _pm, silent=False: located.append(1) or HP
    assert worker._locate_with_retries(object(), "WAITING") == HP
    assert len(located) == 1  # nothing scanned before the game showed
    assert waits == [W.SCREEN_POLL_INTERVAL] * 35


def test_retries_restart_after_a_pre_game_screen(worker, monkeypatch):
    monkeypatch.setattr(W, "LOCATE_MAX_RETRIES", 3)
    screens = iter(["game", "game", "login", "game", "game", "game", "game", "game"])
    monkeypatch.setattr(W, "screen_kind", lambda _pm: next(screens))
    worker._stop_event.wait = lambda _t: False
    calls = []
    worker._locate = lambda _pm, silent=False: calls.append(1) and None
    worker._report_locate_exhausted = lambda _pm: None
    assert worker._locate_with_retries(object(), "WAITING") is None
    assert len(calls) == 2 + (W.LOCATE_MAX_RETRIES + 1)


def test_screen_kind_of_an_unreadable_client_is_unknown():
    from services.login_screen import screen_kind

    assert screen_kind(object()) == "unknown"
