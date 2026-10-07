"""寶箱整理: after a 神武玄天塔 run, open the 關 boxes and tidy what came out.

The 關 boxes (辰星 to 颶星, one per 關) open with a plain `use`: no dialog (live
2026-10-07: 歲星寶箱 -> 聖曦頭盔 within 0.5 s). What each can hold is in the
DB: items.use_case2 is its mystery_box_items table. The user's rules
(2026-10-07), for the things those tables hold:

- potions: keep `keep` of each, eat the rest. One on a 補水 whitelist is a
  supply, not loot, and is never eaten;
- 神兵 in the 蒐藏冊 (collect_book_items) not collected yet: collected first
  (one of each; the hook's collection commands, tthol-hook 2026-10-07). 煉化
  waits for a hook command;
- everything else the warehouse takes (神兵, skill books, 覺醒符, 圖紙): stored;
- what cannot be stored stays in the bag.

Rounds: open until no box is left or the bag is full (BAG_SLOTS), collect, eat, store
(a 補給 trip that stores only these), then open again. It stops with a warning
when a round frees no slot (the warehouse is full too).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from services._paths import bundled
from services.item_catalog import category_for

BOX_NAMES = ("辰星寶箱", "太白寶箱", "熒惑寶箱", "歲星寶箱", "鎮星寶箱", "冽星寶箱", "颶星寶箱")
BAG_SLOTS = 40  # the bag holds 40 stacks (user, 2026-10-07)
CONFIRM_WAIT = 2.0  # for a use to show in the bag
POLL = 0.2
MAX_ROUNDS = 10  # open / eat / store rounds in one tidy
COLLECT_COMMANDS = ("collection", "collectpage", "collect", "openpanel")
COLLECT_WAIT = 5.0  # the 蒐藏冊 flags (one BB per group) after asking for every group


@dataclass(frozen=True)
class Loot:
    boxes: tuple[int, ...]  # the box items, in 關 order
    potions: frozenset[int]  # loot to eat past the keep
    store: frozenset[int]  # loot to store
    names: dict[int, str]  # boxes and loot
    collect: frozenset[int] = frozenset()  # loot in the 蒐藏冊: collected first

    def name(self, item_id: int) -> str:
        return self.names.get(item_id, f"#{item_id}")


def load_loot(db_path: Path | None = None) -> Loot:
    path = db_path or bundled("tthol.sqlite")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        boxes: list[int] = []
        potions: set[int] = set()
        store: set[int] = set()
        names: dict[int, str] = {}
        for name in BOX_NAMES:
            row = con.execute("SELECT id, use_case2 FROM items WHERE name = ?", (name,)).fetchone()
            if row is None or not row[1]:
                continue
            boxes.append(row[0])
            names[row[0]] = name
            for item_id, item_name, code, note, no_store in con.execute(
                "SELECT i.id, i.name, i.type_code, i.note, i.no_store FROM mystery_box_items m"
                " JOIN items i ON i.id = m.ref_id"
                " WHERE m.mystery_id = ? AND m.reward_type = 'item'",
                (row[1],),
            ):
                names[item_id] = item_name
                if category_for(code, note) == "potion":
                    potions.add(item_id)
                elif not no_store:
                    store.add(item_id)
        book = {r[0] for r in con.execute("SELECT item_id FROM collect_book_items")}
    finally:
        con.close()
    kept = frozenset(store - set(boxes))
    return Loot(tuple(boxes), frozenset(potions), kept, names, frozenset(kept & book))


# ---- pure parts --------------------------------------------------------------


def to_eat(
    bag: dict[int, int], loot: Loot, keep: int, supplies: frozenset[int]
) -> list[tuple[int, int]]:
    """(item, how many) of the box potions past `keep`, never a 補水 whitelist one."""
    return [
        (item_id, n - keep)
        for item_id, n in sorted(bag.items())
        if item_id in loot.potions and item_id not in supplies and n > keep
    ]


def to_store(bag: dict[int, int], loot: Loot) -> dict[int, int]:
    return {item_id: n for item_id, n in sorted(bag.items()) if item_id in loot.store and n > 0}


def bag_view(reply: dict) -> tuple[int, dict[int, int]] | None:
    """(stacks used, item -> count) from the hook's `bag` reply."""
    if not reply.get("ok") or not isinstance(reply.get("bag"), list):
        return None
    counts: dict[int, int] = {}
    for slot in reply["bag"]:
        counts[slot["item"]] = counts.get(slot["item"], 0) + slot["count"]
    return len(reply["bag"]), counts


# ---- the run -----------------------------------------------------------------


@dataclass
class TidyResult:
    opened: dict[int, int]  # box -> opened
    got: dict[int, int]  # item -> count that came out
    eaten: dict[int, int]
    stored: int
    collected: dict[int, int]
    boxes_left: int
    problem: str | None  # why it stopped short


class BoxTidy:
    """One tidy. The host gives it its command, wait and log hooks, and the
    store trip (SupplyManager.run with store_only, or None without one)."""

    def __init__(
        self,
        cmd: Callable[[str], dict],
        wait: Callable[[float], None],
        clock: Callable[[], float],
        note: Callable[[str, str], None],
        step: Callable[[str], None],
        loot: Loot,
        keep: int,
        supplies: frozenset[int],
        store_trip: Callable[[dict[int, int]], object] | None,
    ) -> None:
        self.cmd, self.wait, self.clock = cmd, wait, clock
        self.note, self.step, self.name_of = note, step, loot.name
        self.loot, self.keep, self.supplies = loot, keep, supplies
        self.store_trip = store_trip
        self.opened: dict[int, int] = {}
        self.got: dict[int, int] = {}
        self.eaten: dict[int, int] = {}
        self.stored = 0
        self.collected: dict[int, int] = {}
        self.book_done = False  # the 蒐藏冊 looked at this tidy (nothing to do, or no hook)

    def bag(self) -> tuple[int, dict[int, int]]:
        for _ in range(10):
            view = bag_view(self.cmd("bag"))
            if view is not None:
                return view
            self.wait(0.3)
        raise _Short("讀不到背包")

    def use(self, item_id: int, before: int) -> dict[int, int] | None:
        """Send `use` and wait for the item's count to drop; the bag then, or None."""
        r = self.cmd(f"use {item_id}")
        if not r.get("ok"):
            if r.get("error") == "busy":
                self.wait(0.3)
                r = self.cmd(f"use {item_id}")
            if not r.get("ok"):
                return None
        end = self.clock() + CONFIRM_WAIT
        while True:
            self.wait(POLL)
            _slots, counts = self.bag()
            if counts.get(item_id, 0) < before:
                return counts
            if self.clock() >= end:
                return None

    def run(self) -> TidyResult:
        problem, left = None, 0
        try:
            for _ in range(MAX_ROUNDS):
                opens = self.open_all()
                self.collect()
                self.eat()
                self.store()
                slots, counts = self.bag()
                left = sum(counts.get(b, 0) for b in self.loot.boxes)
                if not left:
                    break
                if not opens:
                    problem = f"有寶箱打不開（背包沒有變化），還剩 {left} 個寶箱"
                    break
                # A free slot lets the next round open more (open_all always
                # opens one then); none left after eating and storing: stuck.
                if slots >= BAG_SLOTS:
                    problem = f"背包滿了，存倉也空不出格子，還剩 {left} 個寶箱"
                    break
            else:
                problem = f"整理了 {MAX_ROUNDS} 輪還沒完，先停下，還剩 {left} 個寶箱"
        except _Short as s:
            problem = s.text
        return TidyResult(
            dict(self.opened),
            dict(self.got),
            dict(self.eaten),
            self.stored,
            dict(self.collected),
            left,
            problem,
        )

    def open_all(self) -> bool:
        """Open boxes until none is left or the bag is full (True), or one would
        not open (False)."""
        while True:
            slots, counts = self.bag()
            box = next((b for b in self.loot.boxes if counts.get(b, 0) > 0), None)
            if box is None:
                return True
            # The game opens a box only with a free slot, even the last of a
            # stack (user, 2026-10-07).
            if slots >= BAG_SLOTS:
                self.note("info", f"背包滿了（{BAG_SLOTS} 格），先整理再開")
                return True
            name = self.name_of(box)
            self.step(f"開{name}")
            after = self.use(box, counts[box])
            if after is None:
                self.note("error", f"開{name}沒有反應")
                return False
            self.opened[box] = self.opened.get(box, 0) + 1
            came = self.came(counts, after, box)
            for i, n in came.items():
                self.got[i] = self.got.get(i, 0) + n
            what = (
                "、".join(f"{self.name_of(i)} ×{n}" for i, n in came.items()) or "（沒看到新東西）"
            )
            self.note("confirmed", f"開{name}：{what}")

    def came(self, before: dict[int, int], after: dict[int, int], box: int) -> dict[int, int]:
        """What the box gave: the box count can drop a packet before the loot
        shows (a new stack), so look again for a moment when nothing rose yet."""
        end = self.clock() + CONFIRM_WAIT
        while True:
            came = {
                i: n - before.get(i, 0)
                for i, n in after.items()
                if i != box and n > before.get(i, 0)
            }
            if came or self.clock() >= end:
                return came
            self.wait(POLL)
            _slots, after = self.bag()

    def collect(self) -> None:
        """Box 神兵 the 蒐藏冊 has not got yet: collected, one of each."""
        if self.book_done:
            return
        _slots, counts = self.bag()
        wanted = [i for i in sorted(counts) if i in self.loot.collect and counts[i] > 0]
        if not wanted:
            return
        have = self.book()
        if have is None:
            self.book_done = True
            return
        todo = [i for i in wanted if i not in have]
        if not todo:
            return
        self.step("蒐藏神兵")
        r = self.cmd("openpanel collection")
        if not r.get("ok") or not (r.get("opened") or r.get("open")):
            self.note("error", f"蒐藏冊打不開（{r.get('error') or '沒有打開'}），神兵先存倉")
            self.book_done = True
            return
        try:
            for item_id in todo:
                name = self.name_of(item_id)
                _slots, counts = self.bag()
                before = counts.get(item_id, 0)
                r = self.cmd(f"collect {item_id}")
                if not r.get("ok"):
                    self.note("unconfirmed", f"{name} 沒蒐藏：{r.get('error') or '不明原因'}")
                    continue
                if self.gone(item_id, before):
                    self.collected[item_id] = self.collected.get(item_id, 0) + 1
                    self.note("confirmed", f"蒐藏 {name}")
                else:
                    self.note("unconfirmed", f"蒐藏 {name} 送出了，背包沒少")
        finally:
            self.cmd("closepanel collection")

    def book(self) -> set[int] | None:
        """The ids the 蒐藏冊 has. Right after a login the read has the count but
        no ids: every group is asked for (no window needed), then read again."""
        r = self.cmd("collection")
        if not r.get("ok"):
            self.note(
                "info", f"讀不到蒐藏冊（{r.get('error') or 'hook 沒有蒐藏指令'}），神兵先存倉"
            )
            return None
        have = set(r.get("collected") or [])
        if len(have) >= int(r.get("count") or 0) and not r.get("truncated"):
            return have
        for group in r.get("groups") or []:
            self.cmd(f"collectpage {group}")
        end = self.clock() + COLLECT_WAIT
        while True:
            self.wait(POLL)
            r = self.cmd("collection")
            have = set(r.get("collected") or [])
            if len(have) >= int(r.get("count") or 0) or self.clock() >= end:
                return have

    def gone(self, item_id: int, before: int) -> bool:
        end = self.clock() + CONFIRM_WAIT
        while True:
            self.wait(POLL)
            _slots, counts = self.bag()
            if counts.get(item_id, 0) < before:
                return True
            if self.clock() >= end:
                return False

    def eat(self) -> None:
        _slots, counts = self.bag()
        for item_id, n in to_eat(counts, self.loot, self.keep, self.supplies):
            name = self.name_of(item_id)
            self.step(f"吃掉多的 {name}")
            have, ate = counts[item_id], 0
            for _ in range(n):
                after = self.use(item_id, have)
                if after is None:
                    break
                ate += have - after.get(item_id, 0)
                have = after.get(item_id, 0)
                if have <= self.keep:
                    break
            if ate:
                self.eaten[item_id] = self.eaten.get(item_id, 0) + ate
                self.note("confirmed", f"吃掉 {name} ×{ate}（留 {have}）")
            if ate < n:
                self.note("unconfirmed", f"{name} 吃不下了，先留著")
            _slots, counts = self.bag()

    def store(self) -> None:
        """Store the box loot in the bag."""
        _slots, counts = self.bag()
        want = to_store(counts, self.loot)
        if not want:
            return
        if self.store_trip is None:
            self.note("error", "沒有補給模組，開出來的東西先留在背包")
            return
        self.step("存倉")
        result = self.store_trip(want)
        self.stored += getattr(result, "stored", 0) or 0
        if not getattr(result, "ok", False):
            self.note("error", f"存倉沒完成：{getattr(result, 'detail', '')}")


class _Short(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text
