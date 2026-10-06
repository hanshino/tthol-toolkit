import threading

from services.api_types import DailyQueueConfig, DailySummary
from services.daily import QUEUE_SECTION, DailyQueueManager
from services.guard import GuardStore

NAME = "寒江孤影"


class FakeModule:
    """Runs until `finish` is called; the queue polls `running`."""

    def __init__(self, key, title, done_today=False, start_ok=(True, None)):
        self.key, self.title = key, title
        self.today = done_today
        self.start_ok = start_ok
        self.started = 0
        self.stopped = 0
        self.live = False
        self.result = None
        self.end = threading.Event()
        self.problem = None

    def start(self, pid):
        self.started += 1
        if self.start_ok[0]:
            self.live = True
            self.result = None
        return self.start_ok

    def stop(self, pid):
        self.stopped += 1
        if self.live:
            self.finish("user", "已停止")

    def finish(self, kind="done", reason="ok"):
        self.result = (kind, reason)
        self.live = False
        if kind == "done":
            self.today = True

    def running(self, pid):
        return self.live

    def outcome(self, pid):
        return None if self.live else self.result

    def done_today(self, pid):
        return self.today

    def done_for(self, name):
        return self.today

    def config_problem(self, name):
        return self.problem

    def summary(self, pid):
        if self.live:
            return DailySummary(module=self.key, title=self.title, state="running", step="在跑")
        if self.result and self.result[0] == "error":
            return DailySummary(
                module=self.key, title=self.title, state="stopped", step=self.result[1]
            )
        return DailySummary(
            module=self.key,
            title=self.title,
            state="done_today" if self.today else "idle",
            result="16 層" if self.today else None,
        )


def make(*modules, keys=None):
    store = GuardStore()
    store.save_section(
        NAME, QUEUE_SECTION, DailyQueueConfig(modules=keys or [m.key for m in modules])
    )
    script = {"steps": []}

    def wait(ev, secs):
        # Each poll lets the test script act once (finish a module, stop the queue).
        if script["steps"]:
            script["steps"].pop(0)()
        return ev.is_set()

    mgr = DailyQueueManager(list(modules), character_name=lambda pid: NAME, store=store, wait=wait)
    return mgr, script


def run_to_end(mgr, pid=1):
    ok, reason = mgr.start(pid)
    assert ok, reason
    mgr._passes[pid].thread.join(5)
    assert not mgr._passes[pid].alive()


def states(mgr):
    return [(i.module, i.state) for i in mgr.status(1).items]


def test_runs_items_in_order_and_skips_ones_done_today():
    a, b, c = FakeModule("a", "甲"), FakeModule("b", "乙", done_today=True), FakeModule("c", "丙")
    mgr, script = make(a, b, c)
    script["steps"] = [lambda: a.finish(), lambda: c.finish()]
    run_to_end(mgr)
    assert (a.started, b.started, c.started) == (1, 0, 1)
    assert states(mgr) == [("a", "done"), ("b", "skipped"), ("c", "done")]
    card = mgr.status(1).card
    assert card.state == "done" and card.module is None and card.headline == "3 / 3"


def test_an_error_stops_the_queue_and_start_resumes_from_it():
    a, b = FakeModule("a", "甲"), FakeModule("b", "乙")
    mgr, script = make(a, b)
    script["steps"] = [lambda: a.finish("error", "背包滿")]
    run_to_end(mgr)
    assert states(mgr) == [("a", "error"), ("b", "halted")]
    st = mgr.status(1)
    assert st.card.state == "stopped" and st.card.module == "a"
    assert st.error == "甲停下：背包滿"
    # The fix is in: start again reruns the stopped item even if it now looks done.
    a.today = True
    script["steps"] = [lambda: a.finish(), lambda: b.finish()]
    run_to_end(mgr)
    assert a.started == 2 and b.started == 1
    assert states(mgr) == [("a", "done"), ("b", "done")]
    assert mgr.status(1).error is None


def test_a_module_started_from_its_tab_after_a_stopped_pass_shows_live():
    a, b = FakeModule("a", "甲"), FakeModule("b", "乙")
    mgr, script = make(a, b)
    script["steps"] = [lambda: a.finish("error", "走不到")]
    run_to_end(mgr)
    a.start(1)  # the user starts it again from the 日常 tab, not the queue
    st = mgr.status(1)
    assert states(mgr) == [("a", "running"), ("b", "halted")]
    assert st.card.module == "a" and st.card.state == "running" and st.error is None
    a.finish("error", "新原因")  # stopped again: the overview shows this run's reason
    st = mgr.status(1)
    assert st.card.step == "新原因" and st.error == "甲停下：新原因"
    a.start(1)
    a.finish()
    assert states(mgr) == [("a", "done"), ("b", "halted")]


def test_a_refused_start_is_an_error_with_its_reason():
    a = FakeModule("a", "甲", start_ok=(False, "沒有 hook"))
    b = FakeModule("b", "乙")
    mgr, _ = make(a, b)
    run_to_end(mgr)
    assert states(mgr) == [("a", "error"), ("b", "halted")]
    assert mgr.status(1).card.step == "沒有 hook"
    assert b.started == 0


def test_stop_reaches_the_running_module_and_leaves_it_pending():
    a, b = FakeModule("a", "甲"), FakeModule("b", "乙")
    mgr, script = make(a, b)
    script["steps"] = [lambda: mgr.stop(1)]
    run_to_end(mgr)
    assert a.stopped >= 1 and b.started == 0
    assert states(mgr) == [("a", "pending"), ("b", "pending")]
    card = mgr.status(1).card
    assert card.state == "idle" and "從甲接著跑" in card.where
    assert mgr.status(1).error is None


def test_without_a_pass_items_show_each_modules_own_state():
    a, b = FakeModule("a", "甲", done_today=True), FakeModule("b", "乙")
    mgr, _ = make(a, b)
    st = mgr.status(1)
    assert not st.running
    assert [(i.state, i.result) for i in st.items] == [("done", "16 層"), ("pending", None)]
    assert st.card.state == "idle" and st.card.headline == "1 / 2"
    b.live = True  # started from its own tab
    assert mgr.status(1).card.module == "b"


def test_queue_config_drops_unknown_and_duplicate_modules():
    a = FakeModule("a", "甲")
    mgr, _ = make(a)
    saved = mgr.save_config(1, DailyQueueConfig(modules=["a", "zzz", "a"]))
    assert saved.modules == ["a"]
    assert [m.key for m in mgr.modules()] == ["a"]


def test_empty_queue_will_not_start():
    a = FakeModule("a", "甲")
    mgr, _ = make(a)
    mgr.save_config(1, DailyQueueConfig(modules=[]))
    ok, reason = mgr.start(1)
    assert not ok and "清單是空的" in reason


def test_batch_start_reports_each_character():
    from fastapi.testclient import TestClient

    from services.api import build_app

    class Mgr:
        def start(self, pid):
            return (True, None) if pid == 2 else (False, "角色還沒定位")

    client = TestClient(build_app({"daily_manager": Mgr()}))
    got = client.post("/api/daily/start", json={"pids": [1, 2]}).json()
    assert got == [
        {"pid": 1, "ok": False, "reason": "角色還沒定位"},
        {"pid": 2, "ok": True, "reason": None},
    ]


def test_forget_ends_the_pass_and_drops_it():
    a = FakeModule("a", "甲")
    mgr, script = make(a)
    ok, _ = mgr.start(1)
    assert ok
    mgr.forget(1)
    assert a.stopped >= 1 and 1 not in mgr._passes


def test_precheck_by_name_before_a_login():
    tower, other = FakeModule("tower", "神武玄天塔"), FakeModule("x", "別的")
    mgr, _ = make(tower, other)
    assert mgr.precheck(NAME) == ("run", None)
    tower.problem = "還沒設定攻擊方式"
    assert mgr.precheck(NAME) == ("blocked", "神武玄天塔：還沒設定攻擊方式")
    tower.today = True  # done: its settings no longer matter
    assert mgr.precheck(NAME) == ("run", None)
    other.today = True
    assert mgr.precheck(NAME) == ("done", "今日都做完了")
