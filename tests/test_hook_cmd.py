import pytest

from services.hook_cmd import (
    MAX_LINE,
    CommandChannel,
    NoReply,
    PipeBusy,
    classify_error,
    cmd_pipe_path,
    encode_command,
    parse_reply,
)


def test_encode_adds_newline_and_strips():
    assert encode_command("  use 24008 ") == b"use 24008\n"


@pytest.mark.parametrize("line", ["", "   ", "use 1\nuse 2"])
def test_encode_rejects_lines_the_hook_would_ignore(line):
    with pytest.raises(ValueError):
        encode_command(line)


def test_encode_rejects_over_the_hook_line_cap():
    # The hook drops such a line without replying, which would hang a reader.
    encode_command("x" * MAX_LINE)
    with pytest.raises(ValueError):
        encode_command("x" * (MAX_LINE + 1))


def test_encode_is_ascii_only():
    with pytest.raises(UnicodeEncodeError):
        encode_command("say 你好")


def test_parse_reply():
    assert parse_reply(b'{"ok":true,"item":24007}\n') == {"ok": True, "item": 24007}
    with pytest.raises(ValueError):
        parse_reply(b"[1]\n")


@pytest.mark.parametrize(
    "reply, code",
    [
        ({"ok": True, "item": 1}, None),
        ({"ok": False, "error": "actions not loaded: send reload <path>"}, "actions_not_loaded"),
        ({"ok": False, "error": "timeout: dispatcher did not run"}, "dispatcher_timeout"),
        ({"ok": False, "error": "item not in bag"}, "item_not_in_bag"),
        ({"ok": False, "error": "fault in use"}, "error"),
    ],
)
def test_classify_error(reply, code):
    assert classify_error(reply) == code


class FakePipe:
    def __init__(self, reply: bytes | Exception):
        self.reply = reply
        self.written = []
        self.closed = False

    def write(self, data, timeout):
        self.written.append(data)

    def read_line(self, timeout):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def close(self):
        self.closed = True


def test_send_connects_writes_reads_and_closes():
    pipe = FakePipe(b'{"ok":true,"item":24008}\n')
    paths = []

    def connect(path):
        paths.append(path)
        return pipe

    ch = CommandChannel(connect=connect)
    assert ch.send(4242, "use 24008") == {"ok": True, "item": 24008}
    assert paths == [cmd_pipe_path(4242)]
    assert paths[0].endswith("tthol-cmd-4242")
    assert pipe.written == [b"use 24008\n"]
    assert pipe.closed  # never hold the single pipe instance between commands


def test_send_retries_while_busy_then_gives_up():
    attempts = []

    def connect(path):
        attempts.append(path)
        raise PipeBusy(path)

    ch = CommandChannel(connect=connect, open_timeout=0.0, sleep=lambda _s: None)
    with pytest.raises(PipeBusy):
        ch.send(1, "status")
    assert len(attempts) == 1


def test_send_retries_busy_until_free():
    pipe = FakePipe(b'{"ok":true}\n')
    calls = {"n": 0}

    def connect(path):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PipeBusy(path)
        return pipe

    ch = CommandChannel(connect=connect, open_timeout=5.0, sleep=lambda _s: None)
    assert ch.send(1, "status") == {"ok": True}
    assert calls["n"] == 3


def test_send_unreadable_reply_is_no_reply_and_closes():
    pipe = FakePipe(b"not json\n")
    ch = CommandChannel(connect=lambda _p: pipe)
    with pytest.raises(NoReply):
        ch.send(1, "status")
    assert pipe.closed


def test_send_timeout_propagates_and_closes():
    pipe = FakePipe(NoReply("no reply within the timeout"))
    ch = CommandChannel(connect=lambda _p: pipe)
    with pytest.raises(NoReply):
        ch.send(1, "status")
    assert pipe.closed
