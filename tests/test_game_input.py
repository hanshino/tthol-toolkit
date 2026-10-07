"""Leaving the game: Esc until the system menu is up (live 2026-10-07: the
first Esc only closed a window that was open, and the blind click on 登出遊戲
then landed on the ground)."""

from services import game_input as gi
from services.login_screen import Widget

MENU = [
    Widget(174, "CWndBase", (293, 229, 506, 369)),
    Widget(286, "CWndButton", (357, 270, 440, 289)),
    Widget(1215, "CWndButton", (357, 309, 440, 328)),
]
STATS_PANEL = [Widget(5, "CWndBase", (0, 0, 399, 161))]


def test_the_menu_is_found_by_place_and_size():
    assert gi.menu_in(MENU)
    assert not gi.menu_in(STATS_PANEL)
    assert not gi.menu_in([Widget(9, "CWndButton", (390, 275, 405, 285))])  # no frame, too small


def _input(monkeypatch):
    sent = []
    monkeypatch.setattr(gi, "_resolve_hwnd", lambda pid: 1)
    monkeypatch.setattr(gi, "press_escape", lambda hwnd: sent.append("esc"))
    monkeypatch.setattr(gi, "click_button", lambda hwnd, x, y: sent.append(("click", x, y)))
    return sent


def test_esc_again_when_the_first_only_closed_a_window(monkeypatch):
    sent = _input(monkeypatch)
    screens = iter([False, True])  # 1st Esc closed 屬性, 2nd opened the menu
    assert gi.leave_game(1, menu_open=lambda: next(screens), sleep=lambda s: None)
    assert sent == ["esc", "esc", ("click", *gi.LOGOUT_BUTTON)]


def test_no_blind_click_when_the_menu_never_shows(monkeypatch):
    sent = _input(monkeypatch)
    assert not gi.leave_game(1, menu_open=lambda: False, sleep=lambda s: None)
    assert sent == ["esc"] * gi.ESC_TRIES
