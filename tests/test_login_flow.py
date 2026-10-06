from services.login_flow import LoginFlow
from services.login_screen import (
    MSGBOX,
    MSGBOX_OK,
    PROTECT_EDIT,
    PROTECT_OK,
    SELECT_EXIT,
    SLOT_BUTTONS,
    SLOT_MARKS,
    SLOT_NAMES,
    START_BUTTON,
    Screen,
    Widget,
    classify,
)
from services.login_store import LoginSecrets

SERVERS = ["飛雁山莊(花)", "莫愁谷(魚)", "私服"]
LIST_RECT = (484, 183, 763, 452)


class FakeClient:
    """A client walked through 帳密 -> 選擇角色 -> (保護密碼) -> game by clicks and typing."""

    def __init__(
        self,
        accounts,  # username -> (password, protect or None, [character names])
        kind="login",
        box_on_select=True,
        box_on_slot=False,
        stale_account=0,
        in_game_as=None,
    ):
        self.accounts = accounts
        self.kind = kind
        self.server = -1
        self.texts = {"acct": "x" * stale_account, "pw": "", "protect": ""}
        self.focus = None
        self.user = None
        self.slot = None
        self.box = box_on_select and kind == "select"
        self.box_on_slot = box_on_slot
        self.box_on_select = box_on_select
        self.name = in_game_as  # what the worker reports once located
        self.located = in_game_as is not None
        self.clicks = []
        self.typed = []
        self.rescans = 0
        if kind == "select" and self.user is None:
            self.user = next(iter(accounts))

    # -- what the screen shows ---------------------------------------------------------

    def screen(self, pid):
        if self.kind == "login":
            ws = [
                Widget(1, "CWndStatic", (581, 155, 664, 171), "選擇伺服器"),
                Widget(3787, "CWndEdit", (546, 491, 725, 507), text_len=len(self.texts["acct"])),
                Widget(3788, "CWndEdit", (546, 518, 725, 534), text_len=len(self.texts["pw"])),
                Widget(3807, "CWndButton", (486, 539, 609, 574)),  # 登入
                Widget(3808, "CWndButton", (630, 539, 753, 574)),  # 離開 (closes the client)
            ]
            return Screen(classify(ws, False), ws, list(SERVERS), self.server, LIST_RECT)
        if self.kind in ("select", "protect"):
            names = self.accounts[self.user][2] + ["", "", ""]
            ws = [Widget(239, "CWndStatic", (234, 115, 317, 131), "選擇角色")]
            for i in range(3):
                x = 34 + 165 * i
                ws.append(
                    Widget(SLOT_NAMES[i], "CWndStatic", (x + 58, 325, x + 145, 343), names[i])
                )
                ws.append(Widget(SLOT_BUTTONS[i], "CWndButton", (x, 145, x + 155, 415)))
                ws.append(
                    Widget(
                        SLOT_MARKS[i],
                        "CWndButton",
                        (x + 53, 271, x + 100, 318),
                        value=int(self.slot == i),
                    )
                )
            ws.append(Widget(START_BUTTON, "CWndButton", (46, 426, 153, 452)))
            ws.append(Widget(SELECT_EXIT, "CWndButton", (382, 426, 489, 452)))
            if self.box:
                ws.append(Widget(MSGBOX, "CWndMsgBox", (219, 240, 580, 359)))
                ws.append(Widget(MSGBOX_OK, "CWndButton", (500, 323, 547, 342)))
            if self.kind == "protect":
                ws.append(
                    Widget(
                        PROTECT_EDIT,
                        "CWndEdit",
                        (338, 300, 499, 311),
                        text_len=len(self.texts["protect"]),
                    )
                )
                ws.append(Widget(PROTECT_OK, "CWndButton", (371, 334, 406, 353)))
            return Screen(classify(ws, False), ws)
        if self.kind == "game":
            ws = [Widget(840, "CWndStatic", (452, 583, 503, 597), "個人狀態")]
            return Screen(classify(ws, True), ws)
        return Screen("unknown")

    # -- input ------------------------------------------------------------------------------

    def _hit(self, point):
        x, y = point
        for w in reversed(self.screen(0).widgets):
            left, top, right, bottom = w.rect
            if left <= x <= right and top <= y <= bottom:
                return w
        return None

    def click(self, pid, point):
        self.clicks.append(point)
        if self.kind == "login":
            left, top, right, bottom = LIST_RECT
            if left <= point[0] <= right and top <= point[1] <= bottom:
                row = (point[1] - 192) // 24
                self.server = row if 0 <= row < len(SERVERS) else -1
                return True
        w = self._hit(point)
        if w is None:
            return True
        if w.wid == MSGBOX_OK and self.box:
            self.box = False
        elif self.box:
            return True  # the box takes the click
        elif w.wid == 3787:
            self.focus = "acct"
        elif w.wid == 3788:
            self.focus = "pw"
        elif w.wid == 3808:
            raise AssertionError("clicked 離開: the client would close")
        elif w.wid == 3807:
            self.submit()
        elif w.wid in SLOT_BUTTONS or w.wid in SLOT_MARKS:  # the marker sits inside the slot
            i = (SLOT_BUTTONS + SLOT_MARKS).index(w.wid) % 3
            if self.accounts[self.user][2][i : i + 1]:
                self.slot = i
                self.box = self.box_on_slot
        elif w.wid == START_BUTTON and self.slot is not None:
            protect = self.accounts[self.user][1]
            self.kind = "protect" if protect else "game"
            if self.kind == "game":
                self.enter()
        elif w.wid == SELECT_EXIT:
            self.kind, self.slot = "login", None
        elif w.wid == PROTECT_EDIT:
            self.focus = "protect"
        elif w.wid == PROTECT_OK:
            if self.texts["protect"] == self.accounts[self.user][1]:
                self.enter()
            else:
                self.kind = "login"
        return True

    def submit(self):
        acct = self.accounts.get(self.texts["acct"])
        if self.server != 0 or acct is None or acct[0] != self.texts["pw"]:
            return  # stays on 帳密
        self.user = self.texts["acct"]
        self.kind, self.slot = "select", None
        self.box = self.box_on_select

    def enter(self):
        self.kind = "game"
        self.name = self.accounts[self.user][2][self.slot]
        self.located = False  # the worker has to be rescanned

    def type(self, pid, text):
        self.typed.append(len(text))
        if self.focus:
            self.texts[self.focus] += text
        return True

    def erase(self, pid, n):
        if self.focus:
            self.texts[self.focus] = (
                self.texts[self.focus][:-n] if n < len(self.texts[self.focus]) else ""
            )
        return True

    def logout(self, pid):
        self.kind, self.name, self.located = "login", None, False
        return True

    def rescan(self, pid):
        self.rescans += 1
        self.located = self.kind == "game"

    def character_name(self, pid):
        return self.name if self.located else None


def flow_for(client):
    clock = {"t": 0.0}

    def sleep(ev, secs):
        clock["t"] += secs
        return False

    return LoginFlow(
        client.screen,
        client,
        client.rescan,
        client.character_name,
        clock=lambda: clock["t"],
        sleep=sleep,
    )


def secrets(
    character="債務居士", username="acct1", password="pw1", protect=None, server="飛雁山莊(花)"
):
    return LoginSecrets(character, username, server, password, protect)


ACCOUNTS = {"acct1": ("pw1", None, ["", "債務居士", "酷覓星"]), "acct2": ("pw2", "pp2", ["阿克婭"])}


def test_logs_in_and_locates_the_character():
    client = FakeClient(ACCOUNTS, stale_account=11)
    r = flow_for(client).login(1, secrets())
    assert r.ok, r
    assert client.kind == "game" and client.character_name(1) == "債務居士"
    assert client.server == 0 and client.texts["acct"] == "acct1"  # the old account cleared first
    assert client.rescans >= 1


def test_protect_password_is_typed():
    client = FakeClient(ACCOUNTS)
    r = flow_for(client).login(1, secrets("阿克婭", "acct2", "pw2", protect="pp2"))
    assert r.ok, r


def test_protect_asked_but_not_stored_fails():
    client = FakeClient(ACCOUNTS)
    r = flow_for(client).login(1, secrets("阿克婭", "acct2", "pw2"))
    assert not r.ok and r.reason == "protect"


def test_wrong_password_is_tried_once():
    client = FakeClient(ACCOUNTS)
    r = flow_for(client).login(1, secrets(password="bad"))
    assert not r.ok and r.reason == "rejected"
    assert sum(1 for p in client.clicks if p == (547, 556)) == 1  # 登入 pressed once


def test_character_not_on_the_account_goes_back_to_login():
    client = FakeClient(ACCOUNTS)
    r = flow_for(client).login(1, secrets(character="不存在"))
    assert not r.ok and r.reason == "no-character" and "債務居士" in r.detail
    assert client.kind == "login"


def test_server_missing_from_the_list():
    client = FakeClient(ACCOUNTS)
    r = flow_for(client).login(1, secrets(server="不存在的伺服器"))
    assert not r.ok and r.reason == "server"


def test_ad_box_after_the_slot_click_is_closed():
    client = FakeClient(ACCOUNTS, box_on_slot=True)
    assert flow_for(client).login(1, secrets()).ok


def test_picks_up_at_character_select():
    client = FakeClient({"acct1": ACCOUNTS["acct1"]}, kind="select")
    r = flow_for(client).login(1, secrets())
    assert r.ok and not client.typed  # nothing typed: it was already past 帳密


def test_someone_else_in_game_is_logged_out_first():
    client = FakeClient(ACCOUNTS, kind="game", in_game_as="路人甲")
    client.user = "acct1"
    r = flow_for(client).login(1, secrets())
    assert r.ok and client.character_name(1) == "債務居士"


def test_already_in_game_as_the_character():
    client = FakeClient(ACCOUNTS, kind="game", in_game_as="債務居士")
    r = flow_for(client).login(1, secrets())
    assert r.ok and not client.clicks


def test_a_name_that_never_matches_fails():
    client = FakeClient(ACCOUNTS)
    client.enter_orig = client.enter

    def enter_wrong():
        client.enter_orig()
        client.name = "2"  # a provisional lock's garbage (live 2026-10-06)

    client.enter = enter_wrong
    r = flow_for(client).login(1, secrets())
    assert not r.ok and r.reason == "wrong-character"


def test_logout_from_game_and_from_select():
    client = FakeClient(ACCOUNTS, kind="game", in_game_as="債務居士")
    assert flow_for(client).logout(1) and client.kind == "login"
    client = FakeClient({"acct1": ACCOUNTS["acct1"]}, kind="select")
    assert flow_for(client).logout(1) and client.kind == "login"


def test_stop_ends_the_login():
    import threading

    client = FakeClient(ACCOUNTS)
    stop = threading.Event()
    stop.set()
    r = flow_for(client).login(1, secrets(), stop=stop)
    assert not r.ok and r.reason == "stopped"
