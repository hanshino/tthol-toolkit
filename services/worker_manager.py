from __future__ import annotations

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
from services.process_detector import find_tthol_processes


class WorkerManager:
    def __init__(self) -> None:
        self._sessions: dict[int, CharSession] = {}

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
        # Wired in Task 21
        return SaveSnapshotResult(saved=False)
