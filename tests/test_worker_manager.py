from unittest.mock import patch
from services.api_types import WorldSnapshot
from services.worker_manager import WorkerManager


@patch("services.worker_manager.find_tthol_processes")
def test_world_snapshot_empty_when_no_processes(mock_find):
    mock_find.return_value = []
    wm = WorkerManager()
    snap = wm.world_snapshot()
    assert isinstance(snap, WorldSnapshot)
    assert snap.chars == []


@patch("services.worker_manager.CharSession")
@patch("services.worker_manager.find_tthol_processes")
def test_world_snapshot_emits_placeholder_for_unconnected(mock_find, mock_sess_cls):
    mock_find.return_value = [{"pid": 1234}]
    mock_sess = mock_sess_cls.return_value
    mock_sess.row.return_value = None
    mock_sess.link = "weak"
    mock_sess.last_error = None
    wm = WorkerManager()
    snap = wm.world_snapshot()
    assert len(snap.chars) == 1
    assert snap.chars[0].pid == 1234
    assert snap.chars[0].link == "weak"
    assert snap.chars[0].name == "(連線中)"


@patch("services.worker_manager.find_tthol_processes")
def test_list_characters_returns_processes(mock_find):
    mock_find.return_value = [{"pid": 1234}, {"pid": 5678}]
    wm = WorkerManager()
    chars = wm.list_characters()
    assert len(chars) == 2
    assert chars[0].pid == 1234
    assert chars[0].link == "lost"


@patch("services.worker_manager.CharSession")
@patch("services.worker_manager.find_tthol_processes")
def test_rescan_replaces_dead_session(mock_find, mock_sess_cls):
    mock_find.return_value = [{"pid": 4242}]
    wm = WorkerManager()
    old_sess = mock_sess_cls.return_value
    old_sess.last_hp = 48377
    wm._sessions[4242] = old_sess
    new_sess = type(old_sess)()
    mock_sess_cls.return_value = new_sess

    result = wm.rescan(4242)

    assert result.ok is True
    old_sess.stop.assert_called_once()
    assert wm._sessions[4242] is new_sess
    # The manual HP has to survive the rebuild: with a stale pointer chain it
    # is the only input that can locate at all.
    new_sess.start.assert_called_once_with(hp=48377)


@patch("services.worker_manager.find_tthol_processes")
def test_rescan_rejects_dead_pid(mock_find):
    mock_find.return_value = []
    wm = WorkerManager()
    result = wm.rescan(9999)
    assert result.ok is False
    assert "not running" in (result.error or "").lower()


class _Keep:
    def __init__(self):
        self.started, self.stopped = [], []

    def start(self, pid):
        self.started.append(pid)

    def stop(self, pid):
        self.stopped.append(pid)


@patch("services.worker_manager.threading.Thread")
@patch("services.worker_manager.CharSession")
@patch("services.worker_manager.find_tthol_processes")
def test_keep_active_starts_once_when_located_and_stops_when_gone(
    mock_find, mock_sess_cls, mock_thread
):
    # Run the "background" start inline.
    mock_thread.side_effect = lambda target, args, **_: type(
        "T", (), {"start": lambda self: target(*args)}
    )()
    mock_find.return_value = [{"pid": 7}]
    sess = mock_sess_cls.return_value
    sess.row.return_value = None
    sess.link, sess.last_error, sess.name = "weak", None, ""
    wm = WorkerManager()
    keep = _Keep()
    wm.set_keep_active(keep)
    wm.world_snapshot()
    assert keep.started == []  # not located yet: no window to keep
    sess.name = "寒江孤影"
    wm.world_snapshot()
    wm.world_snapshot()
    assert keep.started == [7]  # once: a manual stop afterwards sticks
    mock_find.return_value = []
    wm.world_snapshot()
    assert keep.stopped == [7]


@patch("services.worker_manager.CharSession")
@patch("services.worker_manager.find_tthol_processes")
def test_another_character_on_the_window_makes_modules_forget(mock_find, mock_sess_cls):
    mock_find.return_value = [{"pid": 7}]
    sess = mock_sess_cls.return_value
    sess.row.return_value = None
    sess.link, sess.last_error, sess.name = "weak", None, "寒江孤影"
    wm = WorkerManager()
    forgot = []
    wm.add_forget(forgot.append)
    wm.world_snapshot()
    sess.name = ""  # logged out, at the character select: no switch yet
    wm.world_snapshot()
    sess.name = "寒江孤影"  # the same one back: nothing to forget
    wm.world_snapshot()
    sess.name = ";w9w"  # one garbage read mid-login
    wm.world_snapshot()
    sess.name = "寒江孤影"
    wm.world_snapshot()
    assert forgot == []
    sess.name = "赫斯提雅"
    wm.world_snapshot()
    wm.world_snapshot()
    assert forgot == [7]
    mock_find.return_value = []  # the window closes
    wm.world_snapshot()
    assert forgot == [7, 7]


@patch("services.worker_manager.threading.Thread")
@patch("services.worker_manager.CharSession")
@patch("services.worker_manager.find_tthol_processes")
def test_a_dispatch_confirmed_switch_is_not_forgotten_again(mock_find, mock_sess_cls, _thread):
    mock_find.return_value = [{"pid": 7}]
    sess = mock_sess_cls.return_value
    sess.row.return_value = None
    sess.link, sess.last_error, sess.name = "weak", None, "寒江孤影"
    wm = WorkerManager()
    forgot = []
    wm.add_forget(forgot.append)
    wm.world_snapshot()
    sess.name = "赫斯提雅"
    wm.confirm_character(7, "赫斯提雅")  # the dispatch logged it in
    assert forgot == [7]
    wm.world_snapshot()
    wm.world_snapshot()
    assert forgot == [7]  # the snapshot does not stop what the dispatch started


@patch("services.worker_manager.threading.Thread")
@patch("services.worker_manager.CharSession")
@patch("services.worker_manager.find_tthol_processes")
def test_family_is_asked_once_per_login_where_the_hook_has_it(
    mock_find, mock_sess_cls, mock_thread
):
    mock_thread.side_effect = lambda target, args, **_: type(
        "T", (), {"start": lambda self: target(*args)}
    )()
    mock_find.return_value = [{"pid": 7}]
    sess = mock_sess_cls.return_value
    sess.row.return_value = None
    sess.link, sess.last_error, sess.name = "weak", None, ""
    feats = {"now": ["chat"]}
    caps = type("C", (), {"features": lambda self, pid: feats["now"]})()
    asked = []
    wm = WorkerManager()
    wm.set_hook_caps(caps)
    wm.set_family_query(lambda pid: asked.append(pid) or {"ok": True})
    wm.world_snapshot()
    sess.name = "寒江孤影"
    wm.world_snapshot()
    assert asked == []  # this hook has no `family`
    feats["now"] = ["chat", "family"]
    wm.world_snapshot()
    wm.world_snapshot()
    assert asked == [7]  # once, not polled
    sess.name = "赫斯提雅"
    wm.world_snapshot()
    wm.world_snapshot()  # a new name is trusted on its second read
    assert asked == [7, 7]  # a new login on the window


@patch("services.worker_manager.find_tthol_processes")
def test_a_snapshot_during_rescan_makes_no_second_session(mock_find):
    """rescan used to pop the session and add the new one later; a snapshot in
    between started a session of its own that rescan then overwrote, and its
    worker ran on unowned (live 2026-10-07, pid 2716: one more per login)."""
    mock_find.return_value = [{"pid": 4242}]
    wm = WorkerManager()
    made = []

    class Sess:
        last_hp = None
        name = None
        link = "weak"
        last_error = None

        def __init__(self, pid):
            made.append(self)
            self.stopped = False
            if len(made) == 2:  # rescan is building its new session: a snapshot lands
                wm.world_snapshot()

        def start(self, hp=None, compat_mode=False):
            pass

        def stop(self):
            self.stopped = True

        def row(self):
            return None

    with patch("services.worker_manager.CharSession", Sess):
        wm.world_snapshot()  # the first session
        wm.rescan(4242)
    assert len(made) == 2
    assert made[0].stopped and not made[1].stopped
    assert wm._sessions[4242] is made[1]
