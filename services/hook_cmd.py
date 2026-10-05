"""Client for a hooked game client's command pipe, \\\\.\\pipe\\tthol-cmd-<pid>.

Protocol (the hook's side, as of hello v4): one ASCII command line ending in
"\\n" in, one JSON line out. The hook keeps reading the same connection after a
reply, but the pipe has a single instance, so a connection held open locks out
every other client (the hook's own scripts included). This client therefore
connects per command and closes right after the reply.

Two hook behaviours shape the error handling:
- a line over 512 bytes is dropped with no reply, so every read has a timeout;
- an action reply of {"ok":true} only means the packet was sent. Callers
  confirm the effect themselves (e.g. the bag count dropping).

This client never sends `reload`: loading the actions DLL is the hook's
deployment step, so "actions not loaded" is reported, not retried.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

PIPE_DIR = "\\\\.\\pipe\\"
CMD_PREFIX = "tthol-cmd-"
MAX_LINE = 511  # the hook drops longer lines (512 including the newline) without answering
OPEN_TIMEOUT = 5.0  # another client may hold the single pipe instance for a moment
OPEN_RETRY = 0.05
REPLY_TIMEOUT = 5.0  # longer than the hook's own 3 s "dispatcher did not run"


class HookCmdError(Exception):
    """Base for command-channel failures that never reached a hook reply."""


class PipeBusy(HookCmdError):
    """The pipe stayed taken by another client for the whole open timeout."""


class PipeGone(HookCmdError):
    """No command pipe for this pid (no hook, or the game exited)."""


class NoReply(HookCmdError):
    """Connected and sent, but no complete reply line arrived in time."""


def cmd_pipe_path(pid: int) -> str:
    return f"{PIPE_DIR}{CMD_PREFIX}{pid}"


def encode_command(line: str) -> bytes:
    """One command line as the hook expects it: ASCII, newline-terminated, within its length cap."""
    text = line.strip()
    if not text:
        raise ValueError("empty command")  # the hook ignores it and never answers
    if "\n" in text or "\r" in text:
        raise ValueError("command must be a single line")
    data = text.encode("ascii")
    if len(data) > MAX_LINE:
        raise ValueError(f"command is {len(data)} bytes, the hook drops lines over {MAX_LINE}")
    return data + b"\n"


def parse_reply(data: bytes) -> dict:
    """The JSON object of one reply line."""
    reply = json.loads(data.decode("utf-8", errors="replace"))
    if not isinstance(reply, dict):
        raise ValueError(f"reply is not an object: {reply!r}")
    return reply


def classify_error(reply: dict) -> str | None:
    """A stable code for the failures callers react to differently, else None."""
    if reply.get("ok"):
        return None
    error = str(reply.get("error", ""))
    if error.startswith("actions not loaded"):
        return "actions_not_loaded"
    if error.startswith("timeout: dispatcher did not run"):
        return "dispatcher_timeout"
    if error == "item not in bag":
        return "item_not_in_bag"
    return "error"


class _Win32Pipe:
    """One overlapped connection, so a read can give up instead of blocking forever."""

    def __init__(self, path: str) -> None:
        import pywintypes
        import win32file

        try:
            self._h = win32file.CreateFile(
                path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error as e:
            if e.winerror == 231:  # ERROR_PIPE_BUSY
                raise PipeBusy(path) from e
            if e.winerror == 2:  # ERROR_FILE_NOT_FOUND
                raise PipeGone(path) from e
            raise HookCmdError(f"open {path}: {e.strerror}") from e

    def _io(self, start: Callable, timeout: float):
        import pywintypes
        import win32event
        import win32file

        ov = pywintypes.OVERLAPPED()
        ov.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            start(ov)
            if (
                win32event.WaitForSingleObject(ov.hEvent, int(timeout * 1000))
                != win32event.WAIT_OBJECT_0
            ):
                win32file.CancelIo(self._h)
                try:
                    # Let the cancelled I/O finish before its buffer and OVERLAPPED go away.
                    win32file.GetOverlappedResult(self._h, ov, True)
                except pywintypes.error:
                    pass
                raise NoReply("no reply within the timeout")
            return win32file.GetOverlappedResult(self._h, ov, False)
        except pywintypes.error as e:
            if e.winerror in (109, 232):  # broken pipe / being closed
                raise NoReply("pipe closed without an answer") from e
            raise HookCmdError(str(e.strerror)) from e
        finally:
            ov.hEvent.Close()

    def write(self, data: bytes, timeout: float) -> None:
        import win32file

        self._io(lambda ov: win32file.WriteFile(self._h, data, ov), timeout)

    def read_line(self, timeout: float) -> bytes:
        import win32file

        deadline = time.monotonic() + timeout
        buf = b""
        while not buf.endswith(b"\n"):
            left = deadline - time.monotonic()
            if left <= 0:
                raise NoReply("no reply within the timeout")
            chunk = win32file.AllocateReadBuffer(
                65536
            )  # one reply in one read, even in message mode
            n = self._io(lambda ov: win32file.ReadFile(self._h, chunk, ov), left)
            if n == 0:
                raise NoReply("pipe closed without an answer")
            buf += bytes(chunk[:n])
        return buf

    def close(self) -> None:
        self._h.Close()


def _win32_connect(path: str) -> _Win32Pipe:
    return _Win32Pipe(path)


class CommandChannel:
    """Per-pid, one command at a time (the hook runs one per frame and has no queue).

    `priority` is accepted for the arbiter later rules will need; with a single
    rule the lock alone is enough.
    """

    def __init__(
        self,
        connect: Callable[[str], object] = _win32_connect,
        open_timeout: float = OPEN_TIMEOUT,
        reply_timeout: float = REPLY_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._connect = connect
        self._open_timeout = open_timeout
        self._reply_timeout = reply_timeout
        self._sleep = sleep
        self._locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, pid: int) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(pid, threading.Lock())

    def send(self, pid: int, line: str, priority: int = 0) -> dict:
        """Run one command and return the hook's reply object (ok or not).

        Raises PipeGone / PipeBusy / NoReply when there is no reply to return.
        """
        del priority
        data = encode_command(line)
        with self._lock(pid):
            pipe = self._open(cmd_pipe_path(pid))
            try:
                pipe.write(data, self._reply_timeout)
                return parse_reply(pipe.read_line(self._reply_timeout))
            except ValueError as e:
                raise NoReply(f"unreadable reply: {e}") from e
            finally:
                pipe.close()

    def _open(self, path: str):
        deadline = time.monotonic() + self._open_timeout
        while True:
            try:
                return self._connect(path)
            except PipeBusy:
                if time.monotonic() >= deadline:
                    raise
                self._sleep(OPEN_RETRY)
