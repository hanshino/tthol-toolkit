from __future__ import annotations

import asyncio
import logging
import time

from services.api_types import (
    AutoClickStatus,
    Character,
    CharacterDetail,
    CharacterRow,
    ConnectRequest,
    ConnectResult,
    Position,
    SaveSnapshotResult,
    Vitals,
    WorldSnapshot,
)
from services.char_session import CharSession
from services.events import WorldStream
from services.process_detector import find_tthol_processes
from services.snapshot_db import SnapshotDB

log = logging.getLogger("tthol.worker_manager")


class WorkerManager:
    def __init__(
        self,
        snapshot_db: SnapshotDB | None = None,
        autoclick_manager=None,
    ) -> None:
        self._sessions: dict[int, CharSession] = {}
        self._db = snapshot_db
        self._autoclick = autoclick_manager

    def set_autoclick_manager(self, mgr) -> None:
        self._autoclick = mgr

    def list_characters(self) -> list[Character]:
        procs = find_tthol_processes()
        out: list[Character] = []
        for p in procs:
            pid = p["pid"]
            sess = self._sessions.get(pid)
            out.append(
                Character(
                    pid=pid,
                    name=sess.name if sess else None,
                    sect=sess.sect if sess else None,
                    level=None,
                    link=sess.link if sess else "lost",
                )
            )
        return out

    def world_snapshot(self) -> WorldSnapshot:
        procs = find_tthol_processes()
        live_pids = {p["pid"] for p in procs}

        for pid in live_pids:
            sess = self._sessions.get(pid)
            if sess is None:
                sess = CharSession(pid)
                self._sessions[pid] = sess
                sess.start()

        for dead_pid in list(self._sessions.keys() - live_pids):
            self._sessions.pop(dead_pid).stop()

        rows = []
        for pid in live_pids:
            sess = self._sessions[pid]
            r = sess.row() or _placeholder_row(pid, sess.link)
            if self._autoclick is not None:
                r = r.model_copy(update={"autoclick": self._autoclick.status(pid)})
            rows.append(r)
        return WorldSnapshot(chars=rows, server_ts=time.time())

    def character_detail(self, pid: int) -> CharacterDetail:
        sess = self._sessions.get(pid)
        if sess is None:
            sess = CharSession(pid)
            self._sessions[pid] = sess
        return sess.detail()

    def connect(self, pid: int, body: ConnectRequest) -> ConnectResult:
        sess = self._sessions.get(pid)
        if sess is None:
            sess = CharSession(pid)
            self._sessions[pid] = sess
        sess.start(hp=body.hp, compat_mode=body.options.compat_mode)
        return ConnectResult(ok=True)

    def disconnect(self, pid: int) -> None:
        sess = self._sessions.pop(pid, None)
        if sess:
            sess.stop()

    def request_inventory_scan(self, pid: int) -> bool:
        sess = self._sessions.get(pid)
        if sess is None:
            return False
        sess.request_inventory()
        return True

    def request_warehouse_scan(self, pid: int) -> bool:
        sess = self._sessions.get(pid)
        if sess is None:
            return False
        sess.request_warehouse()
        return True

    def latest_inventory(self, pid: int) -> list:
        sess = self._sessions.get(pid)
        return list(sess._latest_inv) if sess else []

    def latest_warehouse(self, pid: int) -> list:
        sess = self._sessions.get(pid)
        return list(sess._latest_wh) if sess else []

    def relocate(self, pid: int, hp: int) -> ConnectResult:
        sess = self._sessions.get(pid)
        if sess is None:
            return ConnectResult(ok=False, error="No session for pid")
        sess.stop()
        new_sess = CharSession(pid)
        new_sess.start(hp=hp)
        self._sessions[pid] = new_sess
        return ConnectResult(ok=True)

    def rescan(self, pid: int) -> ConnectResult:
        """Rebuild the session so a dead worker (locate retries exhausted) can try again.

        Used by the manual "重新偵測" UI button when a process appears before the
        user has logged into a character — the initial locate window times out and
        the worker thread exits, leaving the session permanently DISCONNECTED.
        """
        live_pids = {p["pid"] for p in find_tthol_processes()}
        if pid not in live_pids:
            return ConnectResult(ok=False, error="Process not running")
        old = self._sessions.pop(pid, None)
        if old is not None:
            old.stop()
        new_sess = CharSession(pid)
        self._sessions[pid] = new_sess
        new_sess.start()
        return ConnectResult(ok=True)

    def focus(self, pid: int) -> None:
        # Win32 SetForegroundWindow — TODO Task 21+
        pass

    def save_snapshot(self, pid: int, source: str) -> SaveSnapshotResult:
        if self._db is None:
            return SaveSnapshotResult(saved=False)
        sess = self._sessions.get(pid)
        if sess is None or not sess.name:
            return SaveSnapshotResult(saved=False)
        items_payload = sess._latest_inv if source == "inventory" else sess._latest_wh
        items = [{"item_id": i.item_id, "qty": i.quantity} for i in items_payload]
        saved = self._db.save_snapshot(character=sess.name, source=source, items=items)
        return SaveSnapshotResult(saved=saved)

    async def run_tick_loop(self, stream: WorldStream, interval: float = 3.0) -> None:
        """Coroutine: every `interval` seconds, publish current WorldSnapshot.

        Must run on the same event loop as the /ws/world handler — asyncio.Queue
        and asyncio.Lock used inside WorldStream are loop-bound. app.py wires this
        via loop.create_task on the uvicorn server loop.
        """
        while True:
            try:
                await stream.publish(self.world_snapshot())
            except Exception:  # pragma: no cover
                # print() is invisible in a windowed PyInstaller build (no stdout).
                log.exception("tick loop publish error", extra={"cat": "api"})
            await asyncio.sleep(interval)


def _placeholder_row(pid: int, link: str) -> CharacterRow:
    return CharacterRow(
        pid=pid,
        name="(連線中)",
        sect="",
        link=link,  # type: ignore[arg-type]
        level=0,
        vitals=Vitals(hp=0, hp_max=0, mp=0, mp_max=0, weight=0, weight_max=0),
        position=Position(map_name=None, x=0, y=0),
        autoclick=AutoClickStatus(running=False),
    )
