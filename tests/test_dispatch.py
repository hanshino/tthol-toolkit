import threading
import time

from services.api_types import DailyQueueItem, DailyStatus, DailySummary, LoginEntry
from services.dispatch import DispatchManager
from services.login_flow import LoginResult
from services.login_store import LoginSecrets


class Logins:
    def __init__(self, rows):
        # name -> (username, enabled, has_password)
        self.rows = rows

    def get(self, name):
        r = self.rows.get(name)
        if r is None:
            return None
        return LoginEntry(
            character=name, username=r[0], server="飛雁山莊(花)", enabled=r[1], has_password=r[2]
        )

    def secrets(self, name):
        r = self.rows.get(name)
        return LoginSecrets(name, r[0], "飛雁山莊(花)", "pw", None) if r and r[2] else None


class Flow:
    """Logs in at once; `fail` maps a character to a LoginResult."""

    def __init__(self, fail=None):
        self.fail = fail or {}
        self.on = {}  # pid -> character in game
        self.logins = []
        self.logouts = []
        self.lock = threading.Lock()
        self.overlap = []
        self.drops = {}
        self.dropped = []
        self.daily_running = lambda pid: True

    def login(self, pid, who, stop, note):
        if stop.is_set():
            return LoginResult(False, "stopped", "已停止")
        note("登入中")
        with self.lock:
            accounts = {self.account_of.get(c) for p, c in self.on.items() if p != pid}
            if who.username in accounts:
                self.overlap.append(who.character)
            self.logins.append((pid, who.character))
        r = self.fail.get(who.character)
        if r is not None:
            return r
        with self.lock:
            self.on[pid] = who.character
        return LoginResult(True, "ok", "")

    def dismiss(self, pid, stop=None):
        # drops: character -> how many disconnects its 日常 still meets
        name = self.on.get(pid)
        if self.drops.get(name, 0) > 0 and self.daily_running(pid):
            self.drops[name] -= 1
            with self.lock:
                self.on.pop(pid, None)  # back at 帳密
            self.dropped.append(name)
            return "disconnected"
        return "none"

    def logout(self, pid, stop=None):
        time.sleep(0.02)  # a logout takes time: the account is still on until it ends
        with self.lock:
            self.logouts.append(pid)
            self.on.pop(pid, None)
        return True


class Daily:
    """Each run takes `ticks` status polls; `results` maps a character to an outcome."""

    def __init__(self, flow, precheck=None, results=None, ticks=2):
        self.flow = flow
        self.pre = precheck or {}
        self.results = results or {}
        self.ticks = ticks
        self.left = {}
        self.started = []
        self.stopped = []

    def precheck(self, name):
        return self.pre.get(name, ("run", None))

    def start(self, pid):
        name = self.flow.on.get(pid)
        r = self.results.get(name, "done")
        if r == "nostart":
            return False, "還沒設定攻擊方式"
        self.started.append(name)
        self.left[pid] = self.ticks
        return True, None

    def stop(self, pid):
        self.stopped.append(pid)
        self.left[pid] = 0

    def status(self, pid):
        name = self.flow.on.get(pid)
        running = self.left.get(pid, 0) > 0
        if running:
            self.left[pid] -= 1
        r = self.results.get(name, "done")
        state = "running" if running else ("error" if r == "error" else "done")
        return DailyStatus(
            running=running,
            items=[DailyQueueItem(module="tower", title="神武玄天塔", state=state)],
            card=DailySummary(
                title="今日清單", state="running" if running else "done", result="60 層"
            ),
            error="神武玄天塔停下：死亡" if state == "error" else None,
        )


def make(logins, flow=None, daily=None, problems=None, in_game=None, live=None, hook=None):
    flow = flow or Flow()
    flow.account_of = {name: r[0] for name, r in logins.items()}
    flow.on.update(in_game or {})
    daily = daily or Daily(flow)
    confirmed = []
    clock = {"t": 1000.0}  # wall clock, moved on by each wait (the account cooldown)

    def wait(ev, secs):
        with flow.lock:
            clock["t"] += secs
        return ev.wait(0.001)

    mgr = DispatchManager(
        Logins(logins),
        flow,
        daily,
        window_problem=lambda pid: (problems or {}).get(pid),
        confirm_character=lambda pid, name: confirmed.append((pid, name)),
        hook_check=lambda pid: (hook or {}).get(pid),
        in_game_as=lambda pid: (in_game or {}).get(pid),
        live_pids=lambda: list(live or []),
        wall=lambda: clock["t"],
        wait=wait,
    )
    return mgr, flow, daily, confirmed


def finish(mgr, secs=5.0):
    end = time.time() + secs
    while mgr.running() and time.time() < end:
        time.sleep(0.01)
    assert not mgr.running()
    return {r.character: (r.state, r.reason) for r in mgr.status().rows}


ROWS = {
    "甲": ("a1", True, True),
    "乙": ("a2", True, True),
    "丙": ("a3", True, True),
    "丁": ("a4", True, True),
}


def test_every_character_runs_once_across_the_windows():
    mgr, flow, daily, confirmed = make(ROWS)
    ok, _ = mgr.start(list(ROWS), [1, 2])
    assert ok
    rows = finish(mgr)
    assert {k: v[0] for k, v in rows.items()} == dict.fromkeys(ROWS, "done")
    assert sorted(daily.started) == sorted(ROWS)
    assert len(flow.logouts) == 4 and len(confirmed) == 4
    assert {pid for pid, _ in flow.logins} == {1, 2}


def test_rows_without_a_login_or_done_today_are_skipped_without_a_login():
    rows = {**ROWS, "停": ("a5", False, True), "空": ("a6", True, False)}
    flow = Flow()
    daily = Daily(
        flow, precheck={"甲": ("done", "今日都做完了"), "乙": ("blocked", "神武玄天塔：沒設攻擊")}
    )
    mgr, flow, daily, _ = make(rows, flow, daily)
    mgr.start(["甲", "乙", "丙", "停", "空", "無"], [1])
    out = finish(mgr)
    assert out["甲"] == ("skipped", "今日都做完了")
    assert out["乙"] == ("skipped", "神武玄天塔：沒設攻擊")
    assert out["停"][0] == out["空"][0] == out["無"][0] == "skipped"
    assert out["丙"][0] == "done"
    assert [c for _, c in flow.logins] == ["丙"]


def test_a_failed_login_is_noted_and_the_window_moves_on():
    flow = Flow(fail={"甲": LoginResult(False, "rejected", "登入沒有成功")})
    mgr, flow, daily, _ = make(ROWS, flow)
    mgr.start(["甲", "乙"], [1])
    out = finish(mgr)
    assert out["甲"] == ("failed", "登入沒有成功") and out["乙"][0] == "done"
    assert [c for _, c in flow.logins].count("甲") == 1  # never retried


def test_a_daily_error_and_a_start_refusal_are_failures():
    flow = Flow()
    daily = Daily(flow, results={"甲": "error", "乙": "nostart"})
    mgr, flow, daily, _ = make(ROWS, flow, daily)
    mgr.start(["甲", "乙", "丙"], [1])
    out = finish(mgr)
    assert out["甲"] == ("failed", "神武玄天塔停下：死亡")
    assert out["乙"] == ("failed", "還沒設定攻擊方式")
    assert out["丙"][0] == "done"


def test_dry_run_logs_in_and_out_only():
    mgr, flow, daily, _ = make(ROWS)
    mgr.start(["甲", "乙"], [1], dry_run=True)
    out = finish(mgr)
    assert {v[0] for v in out.values()} == {"done"} and daily.started == []
    assert len(flow.logouts) == 2


def test_one_account_is_never_on_two_windows_at_once():
    # Window 2 has nothing else to do: it waits on the account from the start.
    rows = {"甲": ("same", True, True), "乙": ("same", True, True)}
    mgr, flow, daily, _ = make(rows, daily=None)
    mgr.start(["甲", "乙"], [1, 2])
    out = finish(mgr)
    assert {v[0] for v in out.values()} == {"done"} and flow.overlap == []


def test_windows_without_the_hook_take_no_work():
    mgr, flow, daily, _ = make(ROWS, problems={2: "沒有 hook"})
    ok, _ = mgr.start(["甲", "乙"], [1, 2])
    assert ok
    finish(mgr)
    assert {pid for pid, _ in flow.logins} == {1}
    assert next(w for w in mgr.status().windows if w.pid == 2).problem == "沒有 hook"


def test_no_usable_window_does_not_start():
    mgr, *_ = make(ROWS, problems={1: "沒有 hook"})
    ok, reason = mgr.start(["甲"], [1])
    assert not ok and "沒有 hook" in reason


def test_stop_leaves_the_running_character_and_hands_out_no_more():
    flow = Flow()
    daily = Daily(flow, ticks=10**6)
    mgr, flow, daily, _ = make(ROWS, flow, daily)
    mgr.start(["甲", "乙"], [1])
    end = time.time() + 2
    while not daily.started and time.time() < end:
        time.sleep(0.01)
    mgr.stop()
    out = finish(mgr)
    assert out["甲"][0] == "stopped" and out["乙"][0] == "pending"
    # Its 日常 runs on (stopped mid-tower the character would stand and die).
    assert daily.stopped == [] and flow.logouts == []


def test_a_character_already_in_game_runs_on_its_window_first():
    mgr, flow, daily, _ = make(ROWS, in_game={1: "乙"})
    mgr.start(["甲", "乙"], [1])
    finish(mgr)
    assert [c for _, c in flow.logins][0] == "乙"


def test_an_account_in_game_on_one_window_is_not_logged_in_on_another():
    # Window 1 holds 甲 (in game); window 2 is free: it must not take 甲.
    mgr, flow, daily, _ = make(ROWS, in_game={1: "甲"})
    mgr.start(["甲", "乙"], [1, 2])
    out = finish(mgr)
    assert {v[0] for v in out.values()} == {"done"} and flow.overlap == []
    assert (1, "甲") in flow.logins


def test_a_listed_character_on_an_unpicked_window_is_skipped():
    mgr, flow, daily, _ = make(ROWS, in_game={3: "乙"}, live=[1, 3])
    mgr.start(["甲", "乙"], [1])
    out = finish(mgr)
    assert out["乙"][0] == "skipped" and "pid 3" in out["乙"][1]
    assert [c for _, c in flow.logins] == ["甲"]


def test_take_skips_an_account_another_window_holds_from_the_start():
    from services.api_types import DispatchRow
    from services.dispatch import _Window

    mgr, *_ = make(ROWS)
    mgr._rows = [
        DispatchRow(character="甲", state="pending"),
        DispatchRow(character="乙", state="pending"),
    ]
    home, free = _Window(1), _Window(2)
    home.home, home.account = "甲", "a1"  # 甲 is in game on window 1
    mgr._windows = {1: home, 2: free}
    assert mgr._take(free).character == "乙"  # not 甲: its account is on window 1
    assert mgr._take(home).character == "甲"


def test_a_window_whose_hook_cannot_run_stops_after_logging_out():
    mgr, flow, daily, _ = make(
        ROWS, hook={1: "這個視窗的 hook 沒有載入動作指令，請重新載入（reload）"}
    )
    mgr.start(["甲", "乙"], [1])
    out = finish(mgr)
    assert out["甲"][0] == "failed" and "reload" in out["甲"][1]
    assert out["乙"][0] == "pending"  # the window takes no more work
    assert flow.logouts == [1] and daily.started == []
    assert "reload" in mgr.status().windows[0].problem


# ---- hook_problem -------------------------------------------------------------

from services.dispatch import hook_problem  # noqa: E402

NEED = ("status", "walk", "attack")


def hook(replies):
    """send() that answers from a list per command (the last one repeats)."""
    seen = {}

    def send(line):
        seq = replies[line]
        i = seen.get(line, 0)
        seen[line] = i + 1
        r = seq[min(i, len(seq) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    return send


def clocked():
    t = {"now": 0.0}
    return (lambda: t["now"]), (lambda s: t.__setitem__("now", t["now"] + s))


CAPS = {"ok": True, "commands": [{"cmd": c} for c in NEED]}


def test_hook_ready():
    clock, sleep = clocked()
    send = hook({"status": [{"ok": True}], "caps": [CAPS]})
    assert hook_problem(send, NEED, clock, sleep) is None


def test_actions_not_loaded_says_reload():
    clock, sleep = clocked()
    send = hook({"status": [{"ok": False, "error": "actions not loaded: send reload <path>"}]})
    assert "reload" in hook_problem(send, NEED, clock, sleep)


def test_a_hook_that_wakes_up_within_the_wait_is_ready():
    clock, sleep = clocked()
    late = [{"ok": False, "error": "timeout: dispatcher did not run"}] * 4 + [{"ok": True}]
    send = hook({"status": late, "caps": [CAPS]})
    assert hook_problem(send, NEED, clock, sleep) is None


def test_a_hook_that_never_answers_gives_up():
    clock, sleep = clocked()
    send = hook({"status": [{"ok": False, "error": "timeout: dispatcher did not run"}]})
    assert "沒有回應指令" in hook_problem(send, NEED, clock, sleep)
    assert clock() >= 10.0


def test_missing_commands_are_named():
    clock, sleep = clocked()
    send = hook({"status": [{"ok": True}], "caps": [{"ok": True, "commands": [{"cmd": "status"}]}]})
    assert hook_problem(send, NEED, clock, sleep) == "這個 hook 缺少指令：walk、attack"


def test_an_unreachable_pipe():
    clock, sleep = clocked()
    send = hook({"status": [OSError("gone")]})
    assert hook_problem(send, NEED, clock, sleep) == "連不到這個視窗的 hook"


def test_a_disconnect_logs_the_same_character_in_again_and_resumes():
    flow = Flow()
    flow.drops = {"甲": 1}
    daily = Daily(flow, ticks=3)
    flow.daily_running = lambda pid: daily.left.get(pid, 0) > 0
    mgr, flow, daily, _ = make(ROWS, flow, daily)
    mgr.start(["甲", "乙"], [1])
    out = finish(mgr)
    assert out["甲"][0] == "done" and out["乙"][0] == "done"
    assert flow.dropped == ["甲"]
    assert [c for _, c in flow.logins] == ["甲", "甲", "乙"]  # 甲 again, then the next
    assert daily.started.count("甲") == 2 and 1 in daily.stopped


def test_two_disconnects_fail_the_character_and_move_on():
    flow = Flow()
    flow.drops = {"甲": 5}
    daily = Daily(flow, ticks=3)
    flow.daily_running = lambda pid: daily.left.get(pid, 0) > 0
    mgr, flow, daily, _ = make(ROWS, flow, daily)
    mgr.start(["甲", "乙"], [1])
    out = finish(mgr)
    assert out["甲"][0] == "failed" and "連線中斷" in out["甲"][1]
    assert out["乙"][0] == "done"
    assert [c for _, c in flow.logins].count("甲") == 2


# ---- hook v5 (ping) -------------------------------------------------------------

from services.dispatch import RELOAD_HINT, ping_problem  # noqa: E402


def test_ping_rules_out_a_hook_without_its_action_module():
    ready = hook({"ping": [{"ok": True, "v": 5, "actions": True, "in_game": False}]})
    assert ping_problem(ready) is None  # at the login screen: fine
    unloaded = hook({"ping": [{"ok": True, "v": 5, "actions": False, "in_game": False}]})
    assert ping_problem(unloaded) == RELOAD_HINT
    assert ping_problem(hook({"ping": [OSError("gone")]})) == "連不到這個視窗的 hook"


def test_not_in_game_is_waited_out():
    clock, sleep = clocked()
    idle = [{"ok": False, "error": "not in game: the game loop is idle"}] * 3 + [{"ok": True}]
    send = hook({"ping": [{"ok": True, "actions": True}], "status": idle, "caps": [CAPS]})
    assert hook_problem(send, NEED, clock, sleep, ping=True) is None


def test_a_v5_hook_without_actions_fails_at_once():
    clock, sleep = clocked()
    send = hook({"ping": [{"ok": True, "actions": False}], "status": [{"ok": True}]})
    assert hook_problem(send, NEED, clock, sleep, ping=True) == RELOAD_HINT
    assert clock() == 0.0


# --------------------------------------------------------------------------
# 加入派發: a window opened after the start takes from the same queue.
# --------------------------------------------------------------------------


def test_a_window_joining_a_running_dispatch_takes_from_the_queue():
    mgr, flow, daily, _ = make(ROWS)
    daily.ticks = 50
    mgr.start(list(ROWS), [1])
    ok, why = mgr.join(2)
    assert ok, why
    out = finish(mgr)
    assert {v[0] for v in out.values()} == {"done"} and flow.overlap == []
    assert {pid for pid, _ in flow.logins} == {1, 2}
    assert {w.pid for w in mgr.status().windows} == {1, 2}


def test_join_needs_a_running_dispatch_and_a_usable_window():
    mgr, flow, daily, _ = make(ROWS, problems={3: "這個視窗沒有 hook"})
    assert mgr.join(2) == (False, "派發沒在跑")
    daily.ticks = 200
    mgr.start(["甲", "乙"], [1])
    assert mgr.join(3) == (False, "這個視窗沒有 hook")
    assert mgr.join(1) == (False, "這個視窗已經在派發")
    mgr.stop()
    finish(mgr)


def test_join_refuses_a_window_whose_account_is_being_dispatched():
    mgr, flow, daily, _ = make(ROWS, in_game={2: "甲"})
    mgr._rows = []
    from services.dispatch import _Window

    busy = _Window(1)
    busy.account = "a1"
    busy.thread = threading.Thread(target=lambda: time.sleep(0.2))
    busy.thread.start()
    mgr._windows = {1: busy}
    ok, why = mgr.join(2)
    assert not ok and "甲" in why
    busy.thread.join()


def test_rows_held_by_a_joining_window_go_back_in_the_queue():
    mgr, flow, daily, _ = make(ROWS, in_game={3: "乙"}, live=[1, 3])
    daily.ticks = 50
    mgr.start(["甲", "乙"], [1])
    assert {r.character: r.state for r in mgr.status().rows}["乙"] == "skipped"
    ok, why = mgr.join(3)
    assert ok, why
    out = finish(mgr)
    assert out == {"甲": ("done", "60 層"), "乙": ("done", "60 層")}
    assert "乙" in daily.started
    assert flow.overlap == []


def test_a_character_marked_done_during_the_dispatch_is_not_logged_in():
    flow = Flow()
    daily = Daily(flow, ticks=30)
    mgr, flow, daily, _ = make(ROWS, flow, daily)
    mgr.start(["甲", "乙"], [1])
    daily.pre["乙"] = ("done", "今日都做完了")  # marked by hand while 甲 runs
    out = finish(mgr)
    assert out["甲"][0] == "done"
    assert out["乙"][0] == "skipped" and [c for _, c in flow.logins] == ["甲"]


def test_an_account_just_logged_out_waits_before_its_next_login():
    # 甲 and 乙 share an account: 乙 must not log in right after 甲's logout
    # (the game: 資料儲存中，請等待一分鐘).
    from services.dispatch import ACCOUNT_COOLDOWN

    rows = {"甲": ("same", True, True), "乙": ("same", True, True), "丙": ("other", True, True)}
    mgr, flow, daily, _ = make(rows)
    stamps = []
    login = flow.login

    def timed(pid, who, stop, note):
        stamps.append((who.character, mgr._wall()))
        return login(pid, who, stop, note)

    flow.login = timed
    mgr.start(["甲", "乙", "丙"], [1])
    out = finish(mgr)
    assert {v[0] for v in out.values()} == {"done"}
    order = [c for c, _ in stamps]
    assert order == ["甲", "丙", "乙"]  # the other account goes first meanwhile
    at = dict(stamps)
    assert at["乙"] - at["甲"] >= ACCOUNT_COOLDOWN


def test_a_character_left_in_game_off_the_list_is_logged_out_first():
    # Window 1 has 甲 (account a1, not on the list); 乙 shares a1: logging 甲 out
    # starts a1's cooldown, so 丙 goes first.
    rows = {"甲": ("a1", True, True), "乙": ("a1", True, True), "丙": ("a3", True, True)}
    mgr, flow, daily, _ = make(rows, in_game={1: "甲"})
    mgr.start(["乙", "丙"], [1])
    out = finish(mgr)
    assert {v[0] for v in out.values()} == {"done"}
    assert [c for _, c in flow.logins] == ["丙", "乙"]
