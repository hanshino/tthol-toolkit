"""CommandChannel against a real local named pipe: reply, silence, and no server."""

import os
import threading
import time

import pytest

win32pipe = pytest.importorskip("win32pipe")
import win32file  # noqa: E402

from services.hook_cmd import CommandChannel, NoReply, PipeGone  # noqa: E402


def _serve(
    path: str, reply: bytes | None, ready: threading.Event, done: threading.Event, got: list
):
    h = win32pipe.CreateNamedPipe(
        path,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
        1,
        65536,
        65536,
        0,
        None,
    )
    try:
        ready.set()
        win32pipe.ConnectNamedPipe(h, None)
        _, data = win32file.ReadFile(h, 4096)
        got.append(bytes(data))
        if reply is not None:
            win32file.WriteFile(h, reply)
        # Stay silent until the client gives up (or answered), then close.
        done.wait(5)
    finally:
        win32file.CloseHandle(h)


def _start(pid: int, reply: bytes | None):
    path = f"\\\\.\\pipe\\tthol-cmd-{pid}"
    ready, done, got = threading.Event(), threading.Event(), []
    t = threading.Thread(target=_serve, args=(path, reply, ready, done, got), daemon=True)
    t.start()
    assert ready.wait(2)
    return t, done, got


def _test_pid() -> int:
    # Not a real game pid: unique per test run so parallel runs do not collide.
    return 900000 + os.getpid() % 90000


def test_reply_round_trip():
    pid = _test_pid()
    t, done, got = _start(pid, b'{"ok":true,"item":24008}\n')
    try:
        assert CommandChannel(reply_timeout=2.0).send(pid, "use 24008") == {
            "ok": True,
            "item": 24008,
        }
        assert got == [b"use 24008\n"]
    finally:
        done.set()
        t.join(3)


def test_silent_server_times_out_instead_of_hanging():
    # The hook drops an over-long line without answering; the client must give up.
    pid = _test_pid() + 1
    t, done, _ = _start(pid, None)
    try:
        start = time.monotonic()
        with pytest.raises(NoReply):
            CommandChannel(reply_timeout=0.3).send(pid, "status")
        assert time.monotonic() - start < 2.0
    finally:
        done.set()
        t.join(3)


def test_no_server_is_pipe_gone():
    with pytest.raises(PipeGone):
        CommandChannel(open_timeout=0.1).send(_test_pid() + 2, "status")
