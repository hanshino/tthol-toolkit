from __future__ import annotations

import asyncio
import time

from services.api_types import (
    Character,
    CharacterDetail,
    ConnectRequest,
    ConnectResult,
    SaveSnapshotResult,
    WorldSnapshot,
)
from services.char_session import CharSession
from services.events import WorldStream
from services.process_detector import find_tthol_processes
from services.snapshot_db import SnapshotDB


class WorkerManager:
    def __init__(self, snapshot_db: SnapshotDB | None = None) -> None:
        self._sessions: dict[int, CharSession] = {}
        self._db = snapshot_db

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
        rows = []
        for sess in self._sessions.values():
            r = sess.row()
            if r is not None:
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

    def relocate(self, pid: int, hp: int) -> ConnectResult:
        sess = self._sessions.get(pid)
        if sess is None:
            return ConnectResult(ok=False, error="No session for pid")
        sess.stop()
        new_sess = CharSession(pid)
        new_sess.start(hp=hp)
        self._sessions[pid] = new_sess
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

    async def run_tick_loop(self, stream: WorldStream, interval: float = 1.5) -> None:
        """Coroutine: every `interval` seconds, publish current WorldSnapshot.

        Must run on the same event loop as the /ws/world handler — asyncio.Queue
        and asyncio.Lock used inside WorldStream are loop-bound. app.py wires this
        via loop.create_task on the uvicorn server loop.
        """
        while True:
            try:
                await stream.publish(self.world_snapshot())
            except Exception as e:  # pragma: no cover
                print(f"[tick_loop] publish error: {e}")
            await asyncio.sleep(interval)
