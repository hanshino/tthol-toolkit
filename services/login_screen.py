"""Where a game client is between logins, read from its UI window list (RTTI
class names, captions, rects), and the background input that moves it on.

Read-only on game memory. Probed live 2026-10-06 (memory: project_login_screen):

- 帳密 (login): 選擇伺服器 static, the server CWndList, two CWndEdit (帳號
  above 密碼), 登入 = the LEFT image button under them (the right one closes
  the client: never click it). Widget ids change between visits: find these
  by class and position.
- 選擇角色 (select): three slots; name statics ids 63 / 67 / 71, slot click
  buttons 34 / 35 / 36, selected marker 31 / 32 / 33 (+0x20 = 1), 開始遊戲 id
  37, 離開遊戲 id 44 (back to 帳密). Ad / tip boxes: CWndMsgBox id 10000 with
  確定 id 10002.
- 保護密碼 (protect): CWndEdit id 15093, 確定 id 15094.
- In game: the HP chain reads and the bottom bar shows 個人狀態.
"""

from __future__ import annotations

import re
import struct
import threading
import time
from dataclasses import dataclass, field

import reader

# Widget fields (from the widget object).
WID = 0x08
FLAGS = 0x1C  # bit 0: shown
VALUE = 0x20  # a button's checked / selected state
RECT = 0x44  # l, t, r, b in 800x600 game coordinates
CAPTION = 0x124  # char* (Big5); an edit's text
EDIT_LEN = 0x15C
LIST_ITEMS = 0x1A0  # std::vector<item*> begin / end
LIST_SELECTED = 0x1B4
LIST_ITEM_NAME = 0x08  # Big5, inside the item

# Server list rows (measured 2026-10-06): 24 px each, the first from y = 192.
LIST_ROW_TOP = 192
LIST_ROW_H = 24

SLOT_NAMES = (63, 67, 71)
SLOT_BUTTONS = (34, 35, 36)
SLOT_MARKS = (31, 32, 33)
START_BUTTON = 37
SELECT_EXIT = 44
MSGBOX, MSGBOX_OK = 10000, 10002
PROTECT_EDIT, PROTECT_OK = 15093, 15094

# The server list as read 2026-10-06; the 加入自動登入 form offers these.
KNOWN_SERVERS = ("飛雁山莊(花)", "莫愁谷(魚)", "私服")

LOGIN_CAPTION = "選擇伺服器"
SELECT_CAPTION = "選擇角色"
GAME_CAPTION = "個人狀態"


@dataclass(frozen=True)
class Widget:
    wid: int
    cls: str  # e.g. CWndEdit
    rect: tuple[int, int, int, int]
    caption: str = ""
    shown: bool = True
    value: int = 0
    text_len: int | None = None  # CWndEdit only

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2


@dataclass
class Screen:
    kind: str  # login / select / protect / game / unknown
    widgets: list[Widget] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)
    server_selected: int = -1
    list_rect: tuple[int, int, int, int] | None = None

    def by_id(self, wid: int) -> Widget | None:
        return next((w for w in self.widgets if w.wid == wid), None)

    @property
    def box(self) -> Widget | None:
        """The 確定 of an ad / tip box on screen, if one is up."""
        box = self.by_id(MSGBOX)
        ok = self.by_id(MSGBOX_OK)
        return ok if box is not None and box.shown and ok is not None else None

    def edits(self) -> list[Widget]:
        """CWndEdit widgets top to bottom (帳號, 密碼 on the login screen)."""
        return sorted((w for w in self.widgets if w.cls == "CWndEdit"), key=lambda w: w.rect[1])

    def login_button(self) -> Widget | None:
        """登入: the left of the two image buttons right under the password edit.
        The right one closes the client."""
        eds = self.edits()
        if len(eds) < 2:
            return None
        below = eds[1].rect[3]
        row = [
            w
            for w in self.widgets
            if w.cls == "CWndButton"
            and below < w.rect[1] < below + 40
            and w.rect[2] - w.rect[0] > 60
        ]
        return min(row, key=lambda w: w.rect[0]) if len(row) == 2 else None

    def slots(self) -> list[tuple[str, Widget | None, bool]]:
        """(name, click button, selected) per character slot; name "" = empty."""
        out = []
        for name_id, button_id, mark_id in zip(SLOT_NAMES, SLOT_BUTTONS, SLOT_MARKS):
            name = self.by_id(name_id)
            mark = self.by_id(mark_id)
            out.append(
                (name.caption if name else "", self.by_id(button_id), bool(mark and mark.value))
            )
        return out

    def server_point(self, index: int) -> tuple[int, int] | None:
        if self.list_rect is None or not 0 <= index < len(self.servers):
            return None
        left, _top, right, _bottom = self.list_rect
        return (left + right) // 2, LIST_ROW_TOP + LIST_ROW_H * index + LIST_ROW_H // 2


def classify(widgets: list[Widget], in_game: bool) -> str:
    caps = {w.caption for w in widgets if w.cls == "CWndStatic"}
    if LOGIN_CAPTION in caps:
        return "login"
    if any(w.wid == PROTECT_EDIT for w in widgets):
        return "protect"
    if SELECT_CAPTION in caps:
        return "select"
    if in_game and GAME_CAPTION in caps:
        return "game"
    return "unknown"


# ---- reading a live client -----------------------------------------------------


class ScreenReader:
    """read(pid) -> Screen, one pymem handle per pid (opened on first use)."""

    def __init__(self) -> None:
        self._pms: dict[int, object] = {}
        self._lock = threading.Lock()

    def _pm(self, pid: int):
        with self._lock:
            pm = self._pms.get(pid)
            if pm is None:
                import pymem

                pm = pymem.Pymem()
                pm.open_process_from_id(pid)
                self._pms[pid] = pm
            return pm

    def forget(self, pid: int) -> None:
        with self._lock:
            self._pms.pop(pid, None)

    def read(self, pid: int) -> Screen:
        try:
            pm = self._pm(pid)
            widgets, servers, selected, list_rect = _read_widgets(pm)
            try:
                in_game = bool(reader.read_hp_from_player_chain(pm))
            except Exception:
                in_game = False
        except Exception:
            self.forget(pid)  # the process went away, or a stale handle
            return Screen("unknown")
        screen = Screen(classify(widgets, in_game), widgets, servers, selected, list_rect)
        return screen


def _u32(pm, addr: int) -> int:
    return struct.unpack("<I", pm.read_bytes(addr, 4))[0]


def _big5(pm, addr: int, size: int = 40) -> str:
    try:
        raw = pm.read_bytes(addr, size).split(b"\0")[0]
    except Exception:
        return ""
    if not raw or not re.fullmatch(rb"[\x20-\x7e\x81-\xfe]+", raw):
        return ""
    try:
        return raw.decode("big5")
    except UnicodeDecodeError:
        return ""


def _class(pm, obj: int) -> str:
    td = _u32(pm, _u32(pm, _u32(pm, obj) - 4) + 12)
    raw = pm.read_bytes(td + 8, 40).split(b"\0")[0].decode("ascii", "replace")
    return raw[4:-2] if raw.startswith(".?AV") and raw.endswith("@@") else raw


def _read_widgets(pm):
    widgets: list[Widget] = []
    servers: list[str] = []
    selected, list_rect = -1, None
    for obj in reader._window_list(pm):
        if not reader.HEAP_MIN_PTR <= obj <= 0x7FFFFFFF:
            continue
        try:
            cls = _class(pm, obj)
            wid = _u32(pm, obj + WID)
            rect = struct.unpack("<4i", pm.read_bytes(obj + RECT, 16))
            flags = _u32(pm, obj + FLAGS)
            value = struct.unpack("<i", pm.read_bytes(obj + VALUE, 4))[0]
        except Exception:
            continue
        caption, text_len = "", None
        if cls == "CWndEdit":
            try:
                text_len = _u32(pm, obj + EDIT_LEN)
            except Exception:
                text_len = None
        elif cls == "CWndStatic":
            try:
                caption = _big5(pm, _u32(pm, obj + CAPTION))
            except Exception:
                caption = ""
        elif cls == "CWndList":
            servers, selected = _read_list(pm, obj)
            list_rect = rect
        widgets.append(Widget(wid, cls, rect, caption, bool(flags & 1), value, text_len))
    return widgets, servers, selected, list_rect


def _read_list(pm, obj: int) -> tuple[list[str], int]:
    try:
        begin, end = struct.unpack("<2I", pm.read_bytes(obj + LIST_ITEMS, 8))
        count = (end - begin) // 4 if begin and end >= begin else 0
        items = (
            struct.unpack(f"<{count}I", pm.read_bytes(begin, 4 * count)) if 0 < count <= 32 else ()
        )
        names = [_big5(pm, it + LIST_ITEM_NAME) for it in items]
        selected = struct.unpack("<i", pm.read_bytes(obj + LIST_SELECTED, 4))[0]
    except Exception:
        return [], -1
    return names, selected


# ---- background input --------------------------------------------------------------


class GameInput:
    """Clicks and typing posted to the game window (the real cursor does not move)."""

    WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
    VK_BACK, VK_END = 0x08, 0x23

    def click(self, pid: int, point: tuple[int, int]) -> bool:
        from services.auto_click import _resolve_hwnd
        from services.game_input import click_button

        hwnd = _resolve_hwnd(pid)
        if not hwnd:
            return False
        click_button(hwnd, *point)
        return True

    def _key(self, hwnd: int, vk: int, char: int | None = None) -> None:
        from services.game_input import user32

        scan = user32.MapVirtualKeyW(vk, 0)
        user32.PostMessageW(hwnd, self.WM_KEYDOWN, vk, 1 | (scan << 16))
        if char is not None:
            user32.PostMessageW(hwnd, self.WM_CHAR, char, 1 | (scan << 16))
        time.sleep(0.03)
        user32.PostMessageW(hwnd, self.WM_KEYUP, vk, 1 | (scan << 16) | (0xC0 << 24))
        time.sleep(0.05)

    def type(self, pid: int, text: str) -> bool:
        from services.auto_click import _resolve_hwnd
        from services.game_input import user32

        hwnd = _resolve_hwnd(pid)
        if not hwnd:
            return False
        for ch in text:
            user32.PostMessageW(hwnd, self.WM_CHAR, ord(ch), 1)
            time.sleep(0.06)
        return True

    def erase(self, pid: int, count: int) -> bool:
        """End, then `count` backspaces."""
        from services.auto_click import _resolve_hwnd

        hwnd = _resolve_hwnd(pid)
        if not hwnd:
            return False
        self._key(hwnd, self.VK_END)
        for _ in range(count):
            self._key(hwnd, self.VK_BACK, 0x08)
        return True

    def logout(self, pid: int) -> bool:
        from services.game_input import leave_game

        return leave_game(pid)
