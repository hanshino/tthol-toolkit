"""Where a game client is between logins, read from its UI window list (RTTI
class names, captions, rects).

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
from dataclasses import dataclass

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


# Screens before the game: no character exists yet, so locating one is futile.
PRE_GAME = frozenset({"login", "select", "protect"})


# ---- reading a live client -----------------------------------------------------


def screen_kind(pm) -> str:
    """classify() of the client behind `pm`; "unknown" when it cannot be read."""
    try:
        widgets, _servers, _selected, _rect = _read_widgets(pm)
    except Exception:
        return "unknown"
    try:
        in_game = bool(reader.read_hp_from_player_chain(pm))
    except Exception:
        in_game = False
    return classify(widgets, in_game)


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
