"""Log a game client in to one character, and out again, by reading its screen
each step (services/login_screen) and acting on what is there: a client left
on any of 帳密 / 選擇角色 / 保護密碼 / in game is picked up from where it is.

Typed text is never logged; only lengths are compared. One login submit per
call: a refused one is a failure, not retried (a wrong password must not
lock the account).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from services.login_screen import (
    PROTECT_EDIT,
    PROTECT_OK,
    SELECT_EXIT,
    START_BUTTON,
    Screen,
)
from services.login_store import LoginSecrets

log = logging.getLogger("tthol.login")

DEADLINE = 150.0  # one whole login
POLL = 0.4
STEP_WAIT = 30.0  # after a submit / 開始遊戲, for the screen to change
SETTLE = 3.0  # in game this long (map loaded) before the character is located
NAME_WAIT = 25.0  # after the rescan, for the located name
RESCAN_EVERY = 8.0
BOX_TRIES = 4
TYPE_TRIES = 2
LOGOUT_WAIT = 20.0
# A message box in game: 確定 on a dropped connection (連接伺服器失敗。) goes
# back to 帳密 in ~2.5 s (live 2026-10-07); any other box just closes. Its
# text sits at no fixed place, so the screen that follows tells them apart.
BOX_SETTLE = 5.0
MAX_DROPS = 1  # disconnects one login may recover from


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    reason: str  # ok / stopped / no-window / server / rejected / no-character / protect / ...
    detail: str


class _Stop(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason, self.detail = reason, detail


class LoginFlow:
    def __init__(
        self,
        screen: Callable[[int], Screen],
        ui,  # click(pid, point) / type(pid, text) / erase(pid, n) / logout(pid)
        rescan: Callable[[int], object],
        character_name: Callable[[int], str | None],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[threading.Event | None, float], bool] | None = None,
    ) -> None:
        self._screen = screen
        self._ui = ui
        self._rescan = rescan
        self._name = character_name
        self._clock = clock
        self._sleep = sleep or (lambda ev, s: ev.wait(s) if ev is not None else time.sleep(s))

    # -- entry -----------------------------------------------------------------------

    def login(
        self,
        pid: int,
        who: LoginSecrets,
        stop: threading.Event | None = None,
        note: Callable[[str], None] | None = None,
    ) -> LoginResult:
        run = _Run(self, pid, who, stop, note or (lambda _t: None))
        try:
            run.go()
            return LoginResult(True, "ok", f"{who.character} 已進入遊戲")
        except _Stop as s:
            log.info("login pid=%d %s: %s", pid, s.reason, s.detail, extra={"cat": "login"})
            return LoginResult(False, s.reason, s.detail)

    def dismiss(self, pid: int, stop: threading.Event | None = None) -> str:
        """A message box over the game: close it. "disconnected" when that went
        back to 帳密, "closed" for any other box, "none" without one."""
        run = _Run(self, pid, None, stop, lambda _t: None)
        try:
            return run.close_game_box(run.read())
        except _Stop:
            return "none"

    def logout(self, pid: int, stop: threading.Event | None = None) -> bool:
        """Back to the 帳密 screen from in game or 選擇角色. True when it is there."""
        run = _Run(self, pid, None, stop, lambda _t: None)
        try:
            return run.to_login()
        except _Stop:
            return False


class _Run:
    def __init__(self, flow: LoginFlow, pid: int, who: LoginSecrets | None, stop, note) -> None:
        self.f = flow
        self.pid = pid
        self.who = who
        self.stop = stop
        self.note = note
        self.submitted = False  # the login button was pressed once
        self.started = False  # 開始遊戲 was pressed (one entry per login)
        self.protect_sent = False
        self.drops = 0  # disconnects recovered from

    # -- plumbing ----------------------------------------------------------------------

    def wait(self, secs: float) -> None:
        if self.f._sleep(self.stop, secs):
            raise _Stop("stopped", "已停止")

    def read(self) -> Screen:
        if self.stop is not None and self.stop.is_set():
            raise _Stop("stopped", "已停止")
        return self.f._screen(self.pid)

    def click(self, point) -> None:
        if not self.f._ui.click(self.pid, point):
            raise _Stop("no-window", "找不到遊戲視窗")

    def wait_change(self, kinds: set[str], secs: float = STEP_WAIT) -> Screen:
        """Until the screen is one of `kinds` (or the time is up): the last read."""
        end = self.f._clock() + secs
        s = self.read()
        while s.kind not in kinds and self.f._clock() < end:
            self.wait(POLL)
            s = self.read()
        return s

    # -- the login -----------------------------------------------------------------------

    def go(self) -> None:
        end = self.f._clock() + DEADLINE
        while self.f._clock() < end:
            s = self.read()
            if s.kind == "game" and s.box is not None:
                if self.close_game_box(s) == "disconnected":
                    self.dropped()
                continue
            if s.kind == "game":
                if self.in_game():
                    return
                continue
            if s.kind == "select":
                self.on_select(s)
            elif s.kind == "protect":
                self.on_protect(s)
            elif s.kind == "login":
                self.on_login(s)
            else:
                self.wait(POLL)  # loading, or a screen in between
        raise _Stop("timeout", "登入花太久，停止")

    def close_game_box(self, s: Screen) -> str:
        box = s.box
        if s.kind != "game" or box is None:
            return "none"
        self.click(box.center)
        after = self.wait_change({"login"}, BOX_SETTLE)
        if after.kind == "login":
            log.info(
                "login pid=%d disconnected (message box -> 帳密)", self.pid, extra={"cat": "login"}
            )
            return "disconnected"
        return "closed"

    def dropped(self) -> None:
        """Back at 帳密 after a disconnect: the login starts over, once."""
        self.drops += 1
        if self.drops > MAX_DROPS:
            raise _Stop("disconnected", "連線一直中斷（連接伺服器失敗）")
        self.note("連線中斷，重新登入")
        self.submitted = self.started = self.protect_sent = False

    def on_login(self, s: Screen) -> None:
        who = self.who
        if self.submitted:
            box = s.box
            if box is not None:
                self.click(box.center)
            raise _Stop("rejected", "登入沒有成功（帳號密碼錯誤，或伺服器拒絕）")
        if s.box is not None:
            self.click(s.box.center)
            self.wait(0.6)
            return
        try:
            index = s.servers.index(who.server)
        except ValueError:
            raise _Stop("server", f"伺服器清單裡沒有「{who.server}」") from None
        if s.server_selected != index:
            self.note(f"選伺服器 {who.server}")
            self.click(s.server_point(index))
            self.wait(0.6)
            if self.read().server_selected != index:
                raise _Stop("server", f"選不到伺服器「{who.server}」")
        self.note("輸入帳號密碼")
        self.fill(lambda sc: sc.edits()[0] if len(sc.edits()) >= 2 else None, who.username, "帳號")
        self.fill(lambda sc: sc.edits()[1] if len(sc.edits()) >= 2 else None, who.password, "密碼")
        button = self.read().login_button()
        if button is None:
            raise _Stop("screen", "找不到登入按鈕")
        self.click(button.center)
        self.submitted = True
        self.note("登入中")
        self.wait_change({"select", "protect", "game"})

    def fill(self, find: Callable[[Screen], object], text: str, what: str) -> None:
        """Clear the edit, type `text`, check its length; never logs the text."""
        for _ in range(TYPE_TRIES):
            edit = find(self.read())
            if edit is None:
                raise _Stop("screen", f"找不到{what}欄")
            self.click(edit.center)
            self.wait(0.2)
            if edit.text_len:
                self.f._ui.erase(self.pid, edit.text_len + 2)
                self.wait(0.2)
            self.f._ui.type(self.pid, text)
            self.wait(0.3)
            now = find(self.read())
            if now is not None and now.text_len == len(text):
                return
        raise _Stop("typing", f"{what}輸入不進去")

    def on_select(self, s: Screen) -> None:
        box = s.box
        if box is not None:
            self.close_box(s)
            return
        slots = s.slots()
        mine = next(
            (i for i, (name, _b, _m) in enumerate(slots) if name == self.who.character), None
        )
        if mine is None:
            names = "、".join(n for n, _b, _m in slots if n) or "沒有角色"
            self.leave_select(s)
            raise _Stop("no-character", f"這個帳號沒有「{self.who.character}」（{names}）")
        name, button, selected = slots[mine]
        if not selected:
            if button is None:
                raise _Stop("screen", "找不到角色格")
            self.note(f"選角色 {name}")
            self.click(button.center)
            self.wait(0.8)
            return
        if self.started:
            # Pressed once and still here (a box took the click, or the server
            # is slow): wait, then press again only if nothing came of it.
            nxt = self.wait_change({"protect", "game", "login"}, 5.0)
            if nxt.kind != "select":
                return
        start = s.by_id(START_BUTTON)
        if start is None:
            raise _Stop("screen", "找不到開始遊戲按鈕")
        self.note("開始遊戲")
        self.click(start.center)
        self.started = True
        self.wait_change({"protect", "game", "login"})

    def close_box(self, s: Screen) -> None:
        for _ in range(BOX_TRIES):
            box = s.box
            if box is None:
                return
            self.click(box.center)
            self.wait(0.6)
            s = self.read()
        raise _Stop("screen", "訊息框關不掉")

    def leave_select(self, s: Screen) -> None:
        if s.box is not None:
            self.close_box(s)  # a box takes the click
            s = self.read()
        exit_button = s.by_id(SELECT_EXIT)
        if exit_button is not None:
            self.click(exit_button.center)  # 離開遊戲: back to 帳密, not out of the client
            self.wait_change({"login"}, 10.0)

    def on_protect(self, s: Screen) -> None:
        if self.who.protect is None:
            raise _Stop("protect", "這個帳號要保護密碼，但設定裡沒有填")
        if self.protect_sent:
            raise _Stop("protect", "保護密碼沒有通過")
        self.note("輸入保護密碼")
        self.fill(lambda sc: sc.by_id(PROTECT_EDIT), self.who.protect, "保護密碼")
        ok = self.read().by_id(PROTECT_OK)
        if ok is None:
            raise _Stop("screen", "找不到保護密碼的確定")
        self.click(ok.center)
        self.protect_sent = True
        self.wait_change({"game", "login", "select"})

    def in_game(self) -> bool:
        """In game: settle, locate, and check it is the wanted character.
        False sends the loop round again (it logged out of someone else)."""
        want = self.who.character
        now = self.f._name(self.pid)
        if now and now != want and not self.started:
            # Someone else was left in game on this window.
            self.note(f"視窗上是 {now}，先登出")
            if not self.to_login():
                raise _Stop("logout", f"{now} 登不出去")
            return False
        self.note("進入遊戲，等畫面穩定")
        settle_end = self.f._clock() + SETTLE
        while self.f._clock() < settle_end:
            self.wait(POLL)
            if self.read().kind != "game":
                return False
        end = self.f._clock() + NAME_WAIT
        last_scan = None
        seen = None
        while self.f._clock() < end:
            if last_scan is None or self.f._clock() - last_scan >= RESCAN_EVERY:
                self.f._rescan(self.pid)
                last_scan = self.f._clock()
            self.wait(1.0)
            seen = self.f._name(self.pid)
            if seen == want:
                return True
        raise _Stop("wrong-character", f"進遊戲後認到的是「{seen or '讀不到'}」，不是 {want}")

    # -- the logout -------------------------------------------------------------------------

    def to_login(self) -> bool:
        s = self.read()
        if s.kind == "login":
            return True
        if s.kind == "game" and s.box is not None:
            # A box takes the Esc menu's clicks; on a disconnect it is the way out.
            if self.close_game_box(s) == "disconnected":
                return True
            s = self.read()
        if s.kind == "select":
            self.leave_select(s)
        elif s.kind == "game":
            if not self.f._ui.logout(self.pid):
                raise _Stop("no-window", "找不到遊戲視窗")
            self.wait_change({"login"}, LOGOUT_WAIT)
        else:
            self.wait_change({"login", "select", "game"}, 10.0)
        return self.read().kind == "login"
