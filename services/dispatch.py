"""Batch dispatch: hand the ticked characters out to game windows; on each,
log the character in, run its 日常 list, log out, take the next one.

Each window works through the queue on its own thread. A character is
logged in once per dispatch (a wrong password is never retried); whatever
goes wrong with one is written on its row and the window moves on. A window
whose hook is gone (or never there) takes no more work.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from services.api_types import (
    DispatchCandidate,
    DispatchRow,
    DispatchStatus,
    DispatchWindow,
    DispatchWindowOption,
)

log = logging.getLogger("tthol.dispatch")

POLL = 2.0  # while a character's 日常 runs
STOP_WAIT = 30.0  # for the 日常 to wind down after a stop
HOOK_WAIT = 10.0  # after a login, for the hook to start answering commands
MAX_RUN_DROPS = 1  # disconnects while a character's 日常 runs before it counts as failed
DROPPED = object()  # _attempt: the connection dropped, log the same character in again


PING_PROTO = 5  # hooks from this protocol answer `ping` even at the login screens
RELOAD_HINT = "這個視窗的 hook 沒有載入動作指令，請重新載入（reload）"


def ping_problem(send: Callable[[str], dict]) -> str | None:
    """Before a login, a v5+ hook (answers off the game loop):
    {ok, v, actions, reloads, in_game}. Only a missing action module rules
    the window out; not being in game is just the login screen."""
    try:
        reply = send("ping")
    except Exception:
        return "連不到這個視窗的 hook"
    if not reply.get("ok"):
        return f"hook 回報錯誤：{reply.get('error') or 'ping 沒有回應'}"
    if not reply.get("actions"):
        return RELOAD_HINT
    return None


def hook_problem(
    send: Callable[[str], dict],
    needed: tuple[str, ...],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wait: float = HOOK_WAIT,
    ping: bool = False,
) -> str | None:
    """Why the hook in a freshly logged-in client cannot run a module, or None.

    `send(line)` is one hook command (raises when the pipe cannot be reached).
    Asked after the login. Its own errors are passed on in words the user can
    act on (live 2026-10-07: a hook whose action module was never loaded
    failed the tower as "missing every command"). With `ping` (a v5+ hook),
    a missing action module is known at once.

    Waited out: "not in game" (v5: the game loop idle, e.g. a map loading) and
    "dispatcher did not run" (a short stall).
    """
    if ping:
        problem = ping_problem(send)
        if problem is not None:
            return problem
    end = clock() + wait
    last = "hook 沒有回應"
    while True:
        try:
            reply = send("status")
        except Exception:
            reply = {"ok": False, "error": "pipe"}
        error = str(reply.get("error") or "")
        if reply.get("ok"):
            break
        if "actions not loaded" in error:
            return RELOAD_HINT
        if error == "pipe":
            last = "連不到這個視窗的 hook"
        elif error.startswith("not in game"):
            last = "hook 回報還沒進入遊戲（畫面還在載入？）"
        elif "dispatcher did not run" in error:
            last = "hook 沒有回應指令（遊戲畫面沒在更新？）"
        else:
            last = f"hook 回報錯誤：{error}" if error else "hook 沒有回應"
        if clock() >= end:
            return last
        sleep(0.5)
    try:
        caps = send("caps")
    except Exception:
        return "連不到這個視窗的 hook"
    if not caps.get("ok"):
        return f"hook 回報錯誤：{caps.get('error') or '讀不到指令清單'}"
    have = {c.get("cmd") for c in caps.get("commands") or [] if isinstance(c, dict)}
    missing = [c for c in needed if c not in have]
    return f"這個 hook 缺少指令：{'、'.join(missing)}" if missing else None


class _Window:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.character: str | None = None
        self.step: str | None = None
        self.done = 0
        self.problem: str | None = None
        self.thread: threading.Thread | None = None
        # The account logged in here, held until its logout is done: a row is
        # marked done before the logout, and another window must not log the
        # same account in meanwhile.
        self.account: str | None = None
        # The character in game here when the dispatch started: it runs here
        # first if it is on the list, without a logout and a login.
        self.home: str | None = None


class DispatchManager:
    def __init__(
        self,
        logins,  # LoginStore
        flow,  # LoginFlow
        daily,  # DailyQueueManager
        window_problem: Callable[[int], str | None],  # why a window cannot take work
        confirm_character: Callable[[int, str], None],
        # pid -> why its hook cannot run the 日常 after a login (hook_problem), or None
        hook_check: Callable[[int], str | None] = lambda pid: None,
        # pid -> the character in game there now (the screen says so), or None
        in_game_as: Callable[[int], str | None] = lambda pid: None,
        live_pids: Callable[[], list[int]] = lambda: [],
        wall: Callable[[], float] = time.time,
        wait: Callable[[threading.Event, float], bool] = lambda ev, secs: ev.wait(secs),
    ) -> None:
        self._logins = logins
        self._flow = flow
        self._daily = daily
        self._window_problem = window_problem
        self._confirm = confirm_character
        self._hook_check = hook_check
        self._in_game_as = in_game_as
        self._live_pids = live_pids
        self._wall = wall
        self._wait = wait
        self._lock = threading.Lock()
        self._rows: list[DispatchRow] = []
        self._windows: dict[int, _Window] = {}
        self._stop = threading.Event()
        self._dry_run = False
        self._started_at: float | None = None

    # -- control ---------------------------------------------------------------------

    def start(
        self, characters: list[str], pids: list[int], dry_run: bool = False
    ) -> tuple[bool, str | None]:
        if self.running():
            return False, "派發已經在跑"
        windows = list(dict.fromkeys(pids))
        usable = [pid for pid in windows if self._window_problem(pid) is None]
        if not usable:
            reasons = {self._window_problem(pid) for pid in windows} - {None}
            return False, "沒有可以用的遊戲視窗" + (
                f"（{'、'.join(sorted(reasons))}）" if reasons else ""
            )
        rows = [self._first_look(name, dry_run) for name in dict.fromkeys(characters)]
        self._skip_online_elsewhere(rows, windows)
        with self._lock:
            self._rows = rows
            self._dry_run = dry_run
            self._started_at = self._wall()
            self._stop = threading.Event()
            self._windows = {pid: _Window(pid) for pid in windows}
            for pid in windows:
                w = self._windows[pid]
                problem = self._window_problem(pid)
                if problem is not None:
                    w.problem = problem
                    continue
                # Who is in game here holds its account until this window logs it out.
                w.home = self._in_game_as(pid)
                w.account = self._account(w.home) if w.home else None
        log.info(
            "dispatch started %d characters on %s%s",
            len(rows),
            usable,
            " (dry run)" if dry_run else "",
            extra={"cat": "dispatch"},
        )
        for pid in usable:
            w = self._windows[pid]
            w.thread = threading.Thread(
                target=self._work, args=(w,), daemon=True, name=f"dispatch-{pid}"
            )
            w.thread.start()
        return True, None

    def stop(self) -> None:
        """Stop handing out characters and stop the ones running. Characters
        stay logged in where they are."""
        self._stop.set()

    shutdown = stop

    def running(self) -> bool:
        with self._lock:
            return self._alive()

    def _alive(self) -> bool:
        return any(w.thread is not None and w.thread.is_alive() for w in self._windows.values())

    def status(self) -> DispatchStatus:
        with self._lock:
            return DispatchStatus(
                running=self._alive(),
                dry_run=self._dry_run,
                started_at=self._started_at,
                rows=[r.model_copy() for r in self._rows],
                windows=[
                    DispatchWindow(
                        pid=w.pid,
                        character=w.character,
                        step=w.step,
                        done=w.done,
                        problem=w.problem,
                    )
                    for w in self._windows.values()
                ],
            )

    def _skip_online_elsewhere(self, rows: list[DispatchRow], windows: list[int]) -> None:
        """A listed character in game on a window the dispatch was not given:
        logging its account in elsewhere would throw that window out."""
        for pid in self._live_pids():
            if pid in windows:
                continue
            name = self._in_game_as(pid)
            if not name:
                continue
            account = self._account(name)
            for row in rows:
                if row.state != "pending":
                    continue
                if row.character == name or (account and self._account(row.character) == account):
                    row.state = "skipped"
                    row.reason = f"{name} 在沒勾選的視窗（pid {pid}）登著這個帳號"

    def plan(self, pids: list[int]) -> tuple[list, list]:
        """For the dispatch page: each listed character with what a run would do,
        and each window with why it could not take work."""
        candidates = []
        for entry in self._logins.list():
            if not entry.enabled:
                verdict, why = "no-login", "已停用"
            elif not entry.has_password:
                verdict, why = "no-login", "沒有填密碼"
            else:
                verdict, why = self._daily.precheck(entry.character)
            candidates.append(DispatchCandidate(entry=entry, verdict=verdict, reason=why))
        windows = [
            DispatchWindowOption(
                pid=pid, name=self._in_game_as(pid), problem=self._window_problem(pid)
            )
            for pid in pids
        ]
        return candidates, windows

    # -- the queue ----------------------------------------------------------------------

    def _first_look(self, name: str, dry_run: bool) -> DispatchRow:
        """Rows that need no login are settled before any window starts."""
        entry = self._logins.get(name)
        if entry is None:
            return DispatchRow(character=name, state="skipped", reason="帳號清單裡沒有這隻角色")
        if not entry.enabled:
            return DispatchRow(character=name, state="skipped", reason="已停用")
        if not entry.has_password:
            return DispatchRow(character=name, state="skipped", reason="沒有填密碼")
        if not dry_run:
            verdict, why = self._daily.precheck(name)
            if verdict != "run":  # done today, or its settings cannot run
                return DispatchRow(character=name, state="skipped", reason=why)
        return DispatchRow(character=name, state="pending")

    def _take(self, w: _Window) -> DispatchRow | None:
        """The next pending row whose account is not in use on another window
        (one account logs in once at a time); the character already in game
        here comes first."""
        with self._lock:
            busy = {
                other.account
                for other in self._windows.values()
                if other is not w and other.account
            }
            free = [
                r
                for r in self._rows
                if r.state == "pending" and self._account(r.character) not in busy
            ]
            row = next((r for r in free if r.character == w.home), None) or next(iter(free), None)
            w.home = None  # past the first pick, this window logs in whoever is next
            if row is not None:
                w.account = self._account(row.character)
                row.state, row.pid, row.started_at = "login", w.pid, self._wall()
                w.character, w.step = row.character, "登入中"
            return row

    def _account(self, name: str) -> str | None:
        entry = self._logins.get(name)
        return entry.username if entry else None

    def _end(self, w: _Window, row: DispatchRow, state: str, reason: str | None) -> None:
        with self._lock:
            row.state, row.reason, row.ended_at = state, reason, self._wall()
            if state == "done":
                w.done += 1
            w.character, w.step = None, None
        log.info(
            "dispatch pid=%d %s: %s %s",
            w.pid,
            row.character,
            state,
            reason or "",
            extra={"cat": "dispatch"},
        )

    def _give_back(self, w: _Window, row: DispatchRow) -> None:
        with self._lock:
            row.state, row.pid, row.started_at = "pending", None, None
            w.character, w.step = None, None

    def _set_step(self, w: _Window, step: str) -> None:
        with self._lock:
            w.step = step

    def _work(self, w: _Window) -> None:
        try:
            while not self._stop.is_set():
                problem = self._window_problem(w.pid)
                if problem is not None:
                    w.problem = problem
                    return
                row = self._take(w)
                if row is None:
                    if self._waiting_for_account():
                        self._wait(self._stop, POLL)  # its account is busy on another window
                        continue
                    return
                try:
                    more = self._one(w, row)
                finally:
                    with self._lock:
                        # Logged out (or left in game on a stop: the dispatch ends then).
                        w.account = None
                if not more:
                    return
        except Exception:
            log.exception("dispatch pid=%d failed", w.pid, extra={"cat": "dispatch"})
            w.problem = "派發出錯停止（詳見診斷紀錄）"
        finally:
            with self._lock:
                w.character, w.step = None, None
                for r in self._rows:
                    if r.pid == w.pid and r.state in ("login", "running"):
                        # Not left holding its account: other windows wait on it.
                        r.state, r.reason, r.ended_at = "failed", w.problem, self._wall()

    def _waiting_for_account(self) -> bool:
        with self._lock:
            return any(r.state == "pending" for r in self._rows)

    def _one(self, w: _Window, row: DispatchRow) -> bool:
        """One character on this window; False: the window takes no more.

        A disconnect while its 日常 runs logs the same character in again and
        the 日常 picks up where it stopped (the tower resumes its floor), once.
        """
        who = self._logins.secrets(row.character)
        if who is None:
            self._end(w, row, "failed", "讀不到密碼")
            return True
        drops = 0
        while True:
            result = self._attempt(w, row, who)
            if result is not DROPPED:
                return result
            drops += 1
            if drops > MAX_RUN_DROPS:
                self._end(w, row, "failed", "連線中斷兩次，換下一隻")
                return True  # the disconnect left the window at 帳密
            log.info(
                "dispatch pid=%d %s: disconnected, logging in again",
                w.pid,
                row.character,
                extra={"cat": "dispatch"},
            )
            with self._lock:
                row.state = "login"
            self._set_step(w, "連線中斷，重新登入")

    def _attempt(self, w: _Window, row: DispatchRow, who):
        """Log in and run the 日常: True / False as _one, or DROPPED."""
        name = row.character
        result = self._flow.login(w.pid, who, self._stop, note=lambda t: self._set_step(w, t))
        if not result.ok:
            if result.reason == "stopped":
                self._give_back(w, row)
                return False
            self._end(w, row, "failed", result.detail)
            if result.reason == "no-window":
                w.problem = "遊戲視窗不見了"
                return False
            self._flow.logout(w.pid, self._stop)
            return True
        self._confirm(w.pid, name)
        problem = self._window_problem(w.pid)
        if problem is not None:
            self._end(w, row, "failed", f"登入後不能用：{problem}")
            w.problem = problem
            return False
        self._set_step(w, "確認 hook")
        problem = self._hook_check(w.pid)
        if problem is not None:
            # The window's hook, not the character: no more work here.
            self._end(w, row, "failed", problem)
            w.problem = problem
            self._logout(w)
            return False
        if self._dry_run:
            self._end(w, row, "done", "登入確認成功（試跑，沒有跑日常）")
            return self._logout(w)
        ok, why = self._daily.start(w.pid)
        if not ok:
            self._end(w, row, "failed", why or "日常開不起來")
            return self._logout(w)
        with self._lock:
            row.state = "running"
        self._set_step(w, "跑日常")
        while self._daily.status(w.pid).running:
            if self._wait(self._stop, POLL):
                self._stop_daily(w)
                self._end(w, row, "stopped", "手動停下")
                return False  # leave the character where it is
            if self._flow.dismiss(w.pid, self._stop) == "disconnected":
                self._stop_daily(w)
                return DROPPED
        if self._flow.dismiss(w.pid, self._stop) == "disconnected":
            return DROPPED  # the 日常 ended on the dropped connection
        st = self._daily.status(w.pid)
        states = {i.state for i in st.items}
        if st.error or "error" in states:
            self._end(w, row, "failed", st.error or "日常停下")
        elif states <= {"done", "skipped"}:
            self._end(w, row, "done", st.card.result)
        else:
            self._end(w, row, "stopped", "日常沒有跑完")
        return self._logout(w)

    def _stop_daily(self, w: _Window) -> None:
        self._daily.stop(w.pid)
        end = self._wall() + STOP_WAIT
        while self._daily.status(w.pid).running and self._wall() < end:
            self._wait(threading.Event(), 0.5)

    def _logout(self, w: _Window) -> bool:
        self._set_step(w, "登出")
        if self._flow.logout(w.pid, self._stop):
            return True
        if not self._stop.is_set():
            w.problem = "登不出去，這個視窗先停下"
        return False
