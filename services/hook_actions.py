"""Hook actions every module that moves the character shares: walk to a tile,
talk to an NPC and drive its dialog by jump id.

Moved out of the tower (services/tower_run.py) unchanged, so the dungeon runner
uses the same proven steps. An Actor wraps one run's command and wait
functions: `cmd(line) -> reply` (raises the run's stop), `wait(secs)` (raises
the run's stop) and a clock.
"""

from __future__ import annotations

from collections.abc import Callable

from services.tower import pick_option

TILE_PX = 40
WALK_WAIT = 6.0  # after a walk, for the character to get there
TALK_WAIT = 40.0  # for the NPC dialog to open and be driven through


def own_tile(st: dict, objs: list[dict]) -> tuple[int, int]:
    """Own tile from `near` pixels; `status`'s tile goes stale after a same-map
    teleport (it keeps the old tile until the next step).
    """
    me = next((o for o in objs if o.get("h") == st.get("self")), None)
    if me is not None:
        return me["x"] // TILE_PX, me["y"] // TILE_PX
    return st["tile"][0], st["tile"][1]


def walk_line(tile: tuple[int, int]) -> str:
    return f"walk {tile[0] * TILE_PX + TILE_PX // 2} {tile[1] * TILE_PX + TILE_PX // 2}"


class Actor:
    def __init__(
        self,
        cmd: Callable[[str], dict],
        wait: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self.cmd = cmd
        self.wait = wait
        self.clock = clock

    def near(self) -> list[dict]:
        r = self.cmd("near")
        return r.get("objects") or [] if r.get("ok") else []

    def walk(self, tile: tuple[int, int], wait: bool = True, timeout: float = WALK_WAIT) -> None:
        line = walk_line(tile)
        for _ in range(6):  # refused (path false) while the last walk still runs
            if self.cmd(line).get("path"):
                break
            self.wait(0.3)
        if not wait:
            return
        end = self.clock() + timeout
        while self.clock() < end:
            self.wait(0.5)
            st = self.cmd("status")
            if not st.get("ok"):
                continue
            t = own_tile(st, self.near())
            if abs(t[0] - tile[0]) <= 1 and abs(t[1] - tile[1]) <= 1:
                return

    def talk(self, npc: dict, want, avoid, timeout: float = TALK_WAIT, **kw) -> str:
        self.cmd(f"talk {npc['h']}")
        return self.dialog(want, avoid, timeout, **kw)

    def dialog(
        self,
        want,
        avoid,
        timeout: float,
        leave: bool = False,
        once: bool = False,
        fallback: tuple[int, ...] = (),
        leave_id: int | None = None,
        too_low_id: int | None = None,
    ) -> str:
        """Drive the dialog by jump id. map / closed / no-choice / low / stuck / none / timeout / left.

        `once`: each wanted id is chosen at most once; when the dialog offers
        one again (it looped back), only `fallback` ids are chosen from then on.
        `leave`: pick `leave_id` when it is offered. `too_low_id`: the "level too
        low" message, answered with "low".
        """
        st = self.cmd("status")
        me0 = st.get("self")
        end = self.clock() + timeout
        seen = chose = False
        picked: set[int] = set()
        fell_back = False
        while self.clock() < end:
            self.wait(0.2)
            st = self.cmd("status")
            if not st.get("ok") or st.get("self") != me0:
                self.wait(2.0)
                return "map"
            d = self.cmd("dialog")
            if not d.get("open"):
                if seen:
                    return "closed" if chose else "no-choice"
                continue
            seen = True
            if d.get("waiting"):
                continue
            options = d.get("options") or []
            if options:
                if leave and leave_id is not None and leave_id in options:
                    self.cmd(f"option {options.index(leave_id)}")
                    self.wait(2.0)
                    return "left"
                if once:
                    if not fell_back and any(o in picked for o in options):
                        want, fell_back = fallback, True  # looped back: refused
                    i = next((options.index(w) for w in want if w in options), None)
                else:
                    i = pick_option(options, want, avoid)
                if i is None:
                    return "stuck"
                picked.add(options[i])
                self.cmd(f"option {i}")
                chose = True
                continue
            if too_low_id is not None and d.get("next") == too_low_id:
                return "low"
            self.cmd("next")
        return "timeout" if seen else "none"
