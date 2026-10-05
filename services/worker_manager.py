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
    ErrorInfo,
    Position,
    PositionFrame,
    SaveSnapshotResult,
    Vitals,
    WorldSnapshot,
)
from services.buff_tracker import merge_buffs
from services.char_session import CharSession
from services.events import PositionStream, WorldStream
from services.process_detector import find_tthol_processes
from services.snapshot_db import SnapshotDB

log = logging.getLogger("tthol.worker_manager")


class WorkerManager:
    def __init__(
        self,
        snapshot_db: SnapshotDB | None = None,
        autoclick_manager=None,
        hook_hub=None,
        buff_tracker=None,
    ) -> None:
        self._sessions: dict[int, CharSession] = {}
        self._db = snapshot_db
        self._autoclick = autoclick_manager
        self._hook = hook_hub
        self._buffs = buff_tracker

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
            r = sess.row() or _placeholder_row(pid, sess.link, sess.last_error)
            if self._autoclick is not None:
                r = r.model_copy(update={"autoclick": self._autoclick.status(pid)})
            if self._hook is not None:
                r = r.model_copy(update={"hook": self._hook.status(pid)})
            r = self._with_hook_buffs(pid, r)
            rows.append(r)
        return WorldSnapshot(chars=rows, server_ts=time.time())

    def character_detail(self, pid: int) -> CharacterDetail:
        sess = self._sessions.get(pid)
        if sess is None:
            sess = CharSession(pid)
            self._sessions[pid] = sess
        return self._with_hook_buffs(pid, sess.detail())

    def _with_hook_buffs(self, pid: int, model):
        """Replace the memory-array buffs with the hook's list when it has one."""
        if self._buffs is None:
            return model
        hooked = self._buffs.buffs(pid)
        if hooked is None:
            return model
        return model.model_copy(update={"buffs": merge_buffs(hooked, model.buffs)})

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

    def walk_sample(self, pid: int) -> tuple[int, int, int, int, int] | None:
        """(stage_id, x, y, target_px, target_py) for the click-to-walk runner, or None."""
        sess = self._sessions.get(pid)
        return sess.walk_sample() if sess is not None else None

    def live_handle(self, pid: int):
        """(pm, hp_addr) of a located character for background readers, or None."""
        sess = self._sessions.get(pid)
        return sess.live_handle() if sess is not None else None

    def read_locked(self, pid: int, read):
        """read(pm, hp_addr, compat_mode) on a located character, or None."""
        sess = self._sessions.get(pid)
        return sess.read_locked(read) if sess is not None else None

    def character_name(self, pid: int) -> str | None:
        """Name of the character located in this client, or None."""
        sess = self._sessions.get(pid)
        return (sess.name or None) if sess is not None else None

    def live_pids(self) -> list[int]:
        return list(self._sessions)

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
        hp = None
        if old is not None:
            hp = old.last_hp
            old.stop()
        new_sess = CharSession(pid)
        self._sessions[pid] = new_sess
        # Carry the manual HP over: when the pointer chain has gone stale it is
        # the only input that can locate, and dropping it made 重偵 a button
        # that could never succeed.
        new_sess.start(hp=hp)
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

    def position_frame(
        self, sent: dict[int, tuple[int, int]] | None = None
    ) -> PositionFrame | None:
        """Positions that changed since `sent` (all known ones when None).

        `sent` maps pid -> (session id, seq) and is updated in place; keying on
        the session too means a rebuilt session (重偵), whose seq restarts, is
        still picked up. Pids whose session is gone are forgotten. Returns None
        when nothing changed, so an idle game sends no frames.
        """
        sessions = dict(self._sessions)
        pos: dict[int, Position] = {}
        for pid, sess in sessions.items():
            seq, p = sess.position()
            if seq == 0:
                continue  # no fast sample yet
            mark = (id(sess), seq)
            if sent is None or sent.get(pid) != mark:
                pos[pid] = p
                if sent is not None:
                    sent[pid] = mark
        if sent is not None:
            for pid in sent.keys() - sessions.keys():
                del sent[pid]
        if sent is not None and not pos:
            return None
        return PositionFrame(pos=pos)

    async def run_position_loop(self, stream: PositionStream, interval: float = 0.1) -> None:
        """Coroutine: every `interval` seconds, publish the positions that moved.

        One frame per tick however many clients are open; nothing when idle.
        Pulls from the sessions, so worker threads never touch the event loop.
        Same loop constraint as run_tick_loop.
        """
        sent: dict[int, tuple[int, int]] = {}
        while True:
            try:
                frame = self.position_frame(sent)
                if frame is not None:
                    await stream.publish(frame)
            except Exception:  # pragma: no cover
                log.exception("position loop publish error", extra={"cat": "api"})
            await asyncio.sleep(interval)


def _placeholder_row(pid: int, link: str, last_error: ErrorInfo | None = None) -> CharacterRow:
    """Stand-in for a character that has not located yet.

    It must carry last_error: row() is None precisely while a character has
    never located, which is exactly when there is an error worth showing. An
    earlier version dropped it, so the dashboard could only display errors for
    characters that were already working.
    """
    return CharacterRow(
        pid=pid,
        name="(連線中)",
        sect="",
        link=link,  # type: ignore[arg-type]
        last_error=last_error,
        level=0,
        vitals=Vitals(hp=0, hp_max=0, mp=0, mp_max=0, weight=0, weight_max=0),
        position=Position(map_name=None, x=0, y=0),
        autoclick=AutoClickStatus(running=False),
    )
