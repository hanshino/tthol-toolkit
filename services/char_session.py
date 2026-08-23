"""One CharSession per PID. Owns the worker thread and the latest snapshot."""

from __future__ import annotations

import threading
import time
from typing import Literal

from services import diagnostics
from services.api_types import (
    AutoClickStatus,
    BuffInfo,
    CharacterDetail,
    CharacterRow,
    CharacterStats,
    ErrorInfo,
    Item,
    Position,
    Vitals,
)
from services.worker import ReaderWorker

# Worker emits stats with raw Chinese labels from knowledge.json + the two
# string keys "角色名稱" / "地圖名稱". Translate to English snake_case so the
# Pydantic API models can consume them.
_FIELD_MAP: dict[str, str] = {
    "角色名稱": "name",
    "地圖名稱": "map_name",
    "等級": "level",
    "血量": "hp",
    "最大血量": "hp_max",
    "真氣": "mp",
    "最大真氣": "mp_max",
    "負重": "weight",
    "最大負重": "weight_max",
    "X座標": "x",
    "Y座標": "y",
    "外功": "waigong",
    "內力": "neili",
    "根骨": "genggu",
    "身法": "shenfa",
    "技巧": "jiqiao",
    "玄學": "xuanxue",
    "物攻": "wugong",
    "物攻(基礎?)": "wugong_base",
    "內勁": "neijing",
    "防禦": "fangyu",
    "護勁": "huji",
    "命中": "mingzhong",
    "閃躲": "shanduo",
    "魅力值": "charm",
}


class CharSession:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.name: str | None = None
        self.sect: str | None = None
        self._state: str = "DISCONNECTED"
        self._latest_stats: dict[str, int] = {}
        self._latest_inv: list[Item] = []
        self._latest_wh: list[Item] = []
        self._latest_buffs: list[BuffInfo] = []
        self._inv_seq: int = 0
        self._wh_seq: int = 0
        self._last_error: ErrorInfo | None = None
        self._last_hp: int | None = None
        self._log = diagnostics.bind(pid)
        self._lock = threading.Lock()
        self._worker = self._make_worker()

    def _make_worker(self) -> ReaderWorker:
        return ReaderWorker(
            pid=self.pid,
            on_state=self._on_state,
            on_stats=self._on_stats,
            on_inventory=self._on_inv,
            on_warehouse=self._on_wh,
            on_error=self._on_error,
            on_buffs=self._on_buffs,
        )

    @property
    def last_hp(self) -> int | None:
        """The manual HP this session was last started with, if any.

        Kept so a rebuild can reuse it: with the pointer chain stale, a manual
        HP is the only input that can locate at all.
        """
        return self._last_hp

    def start(self, hp: int | None = None, compat_mode: bool = False) -> None:
        if hp is not None:
            self._last_hp = hp
        if self._worker.is_alive():
            # Already running: only refresh the inputs. Replacing a live worker
            # would orphan its thread.
            self._worker._hp_value = hp
            self._worker._compat_mode = compat_mode
            return
        if self._worker.has_run():
            # A thread that ran and exited still reports is_alive() False, but
            # Thread.start() may only be called once -- the old guard let the
            # second call raise RuntimeError, so every reconnect after a locate
            # timeout returned 500 with no way back short of restarting the app.
            self._worker = self._make_worker()
        self._worker._hp_value = hp
        self._worker._compat_mode = compat_mode
        self._worker.start()

    def stop(self) -> None:
        self._worker.stop()

    def request_inventory(self) -> None:
        self._worker.request_inventory()

    def request_warehouse(self) -> None:
        self._worker.request_warehouse()

    @property
    def link(self) -> Literal["ok", "weak", "lost"]:
        if self._state == "LOCATED":
            return "ok"
        if self._state in ("CONNECTING", "WAITING", "RESCANNING", "READ_ERROR"):
            return "weak"
        return "lost"

    def row(self) -> CharacterRow | None:
        with self._lock:
            if not self.name:
                return None
            s = self._latest_stats
            return CharacterRow(
                pid=self.pid,
                name=self.name,
                sect=self.sect or "",
                link=self.link,
                level=s.get("level", 0),
                vitals=Vitals(
                    hp=s.get("hp", 0),
                    hp_max=s.get("hp_max", 0),
                    mp=s.get("mp", 0),
                    mp_max=s.get("mp_max", 0),
                    weight=s.get("weight", 0),
                    weight_max=s.get("weight_max", 0),
                ),
                position=Position(map_name=s.get("map_name"), x=s.get("x", 0), y=s.get("y", 0)),
                autoclick=AutoClickStatus(running=False),
                buffs=list(self._latest_buffs),
                last_error=self._last_error,
            )

    def detail(self) -> CharacterDetail:
        with self._lock:
            s = self._latest_stats
            return CharacterDetail(
                pid=self.pid,
                name=self.name or "",
                sect=self.sect or "",
                link=self.link,
                stats=CharacterStats(
                    level=s.get("level", 0),
                    waigong=s.get("waigong", 0),
                    neili=s.get("neili", 0),
                    genggu=s.get("genggu", 0),
                    shenfa=s.get("shenfa", 0),
                    jiqiao=s.get("jiqiao", 0),
                    xuanxue=s.get("xuanxue", 0),
                    wugong=s.get("wugong", 0),
                    wugong_base=s.get("wugong_base", 0),
                    neijing=s.get("neijing", 0),
                    fangyu=s.get("fangyu", 0),
                    huji=s.get("huji", 0),
                    mingzhong=s.get("mingzhong", 0),
                    shanduo=s.get("shanduo", 0),
                ),
                vitals=Vitals(
                    hp=s.get("hp", 0),
                    hp_max=s.get("hp_max", 0),
                    mp=s.get("mp", 0),
                    mp_max=s.get("mp_max", 0),
                    weight=s.get("weight", 0),
                    weight_max=s.get("weight_max", 0),
                ),
                position=Position(map_name=s.get("map_name"), x=s.get("x", 0), y=s.get("y", 0)),
                autoclick=AutoClickStatus(running=False),
                buffs=list(self._latest_buffs),
                inventory=self._latest_inv or None,
                warehouse=self._latest_wh or None,
                last_error=self._last_error,
            )

    @property
    def last_error(self) -> ErrorInfo | None:
        with self._lock:
            return self._last_error

    # Worker callbacks ------------------------------------------------------

    def _on_error(
        self,
        msg: str,
        *,
        cat: str = "worker",
        code: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Record the worker's reason for failing.

        This used to be `lambda _msg: None`, which destroyed every specific
        failure reason at the moment it was produced -- the single line that
        made "data fetch failed" reports impossible to diagnose.
        """
        with self._lock:
            self._last_error = ErrorInfo(ts=time.time(), message=msg, cat=cat, code=code)
        self._log.error(msg, extra={"cat": cat, "code": code, "detail": detail})

    def _on_state(self, s: str) -> None:
        with self._lock:
            self._state = s

    def _on_stats(self, rows: list[tuple[str, int]]) -> None:
        with self._lock:
            translated: dict[str, int] = {}
            for label, value in rows:
                key = _FIELD_MAP.get(label)
                if key is not None:
                    translated[key] = value
            self._latest_stats = translated
            name = translated.get("name")
            if isinstance(name, str) and name:
                if name != self.name:
                    self._log = diagnostics.bind(self.pid, name)
                self.name = name

    def _on_buffs(self, items: list[tuple[int, str, str]]) -> None:
        with self._lock:
            self._latest_buffs = [BuffInfo(group=g, name=n, kind=k) for g, n, k in items]

    def _on_inv(self, items: list[tuple[int, int, str]]) -> None:
        with self._lock:
            self._latest_inv = [
                Item(item_id=iid, name=name, quantity=qty, source="inventory")
                for iid, qty, name in items
            ]
            self._inv_seq += 1

    def _on_wh(self, items: list[tuple[int, int, str]]) -> None:
        with self._lock:
            self._latest_wh = [
                Item(item_id=iid, name=name, quantity=qty, source="warehouse")
                for iid, qty, name in items
            ]
            self._wh_seq += 1
