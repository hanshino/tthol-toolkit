"""Tests for the hook pipe chat reader (services.hook_hub). Events are fed
straight into the hub, no pipe involved."""

import pytest

from services import hook_hub
from services.hook_hub import HookHub, decode_chat

ME = bytes.fromhex("0100645e000007000000")  # kind 1, npc 24164, instance 7
OTHER = bytes.fromhex("0100625e000009000000")


def _chat(key: bytes, channel: int, name: str, text: str, echo: int = 0) -> bytes:
    field = name.encode("cp950") + b"\0"
    field += b" 21:17:"[: 16 - len(field)]  # stale bytes after the NUL
    field = field.ljust(16, b"\0")
    return b"\x0e" + key + bytes([channel, echo]) + field + text.encode("cp950") + b"\0"


def _msg(
    raw: bytes, ts_us: int = 1_790_000_000_000_000, self_key: bytes | None = ME, type_: int = 900
) -> dict:
    return {
        "t": "msg",
        "seq": 1,
        "ts_us": ts_us,
        "type": type_,
        "self": 12,
        "self_key": self_key.hex() if self_key else None,
        "raw": raw.hex(),
    }


def test_decode_chat_reads_name_and_text():
    pkt = decode_chat(_chat(OTHER, 3, "小明", "我先去補藥"))
    assert pkt is not None
    assert (pkt.key, pkt.channel, pkt.echo, pkt.name, pkt.text) == (
        OTHER,
        "party",
        False,
        "小明",
        "我先去補藥",
    )


def test_decode_chat_keeps_unknown_channel_codes():
    assert decode_chat(_chat(OTHER, 9, "x", "y")).channel == "9"


@pytest.mark.parametrize("raw", [b"\x43" + b"\0" * 40, b"\x0e" + b"\0" * 10])
def test_decode_chat_rejects_other_packets(raw):
    assert decode_chat(raw) is None


def test_hub_keeps_chat_and_drops_everything_else():
    hub = HookHub(list_pids=lambda: [])
    hub._ingest(5, {"t": "hello", "v": 4})
    hub._ingest(5, _msg(b"\x43" + b"\0" * 40))  # attack result
    hub._ingest(5, _msg(_chat(OTHER, 1, "a", "b"), type_=901))  # not a game packet
    hub._ingest(5, {"t": "hb"})
    hub._ingest(5, _msg(_chat(OTHER, 1, "路人", "有人要組蠍子嗎")))
    hub._ingest(5, _msg(_chat(ME, 2, "小明", "還在", echo=1)))
    hub._ingest(5, _msg(_chat(b"\0" * 10, 4, "家族成員", "集合")))

    log = hub.chat(5)
    assert log.connected and log.proto == 4 and log.last_seq == 3
    assert [(m.seq, m.channel, m.echo, m.own, m.name) for m in log.messages] == [
        (1, "normal", False, False, "路人"),
        (2, "whisper", True, True, "小明"),
        (3, "family", False, False, "家族成員"),
    ]
    assert log.messages[0].ts == 1_790_000_000.0
    assert hub.status(5).proto == 4


def test_hub_returns_only_newer_messages():
    hub = HookHub(list_pids=lambda: [])
    for i in range(3):
        hub._ingest(5, _msg(_chat(OTHER, 1, "a", str(i))))
    assert [m.text for m in hub.chat(5, after=1).messages] == ["1", "2"]
    assert hub.chat(5, after=3).messages == []
    # A client holding seqs from an earlier run gets the whole log.
    assert len(hub.chat(5, after=99).messages) == 3


def test_hub_keeps_the_last_500(monkeypatch):
    monkeypatch.setattr(hook_hub, "KEEP", 3)
    hub = HookHub(list_pids=lambda: [])
    for i in range(5):
        hub._ingest(5, _msg(_chat(OTHER, 1, "a", str(i))))
    log = hub.chat(5)
    assert log.last_seq == 5 and [m.text for m in log.messages] == ["2", "3", "4"]


def test_unknown_pid_is_not_connected():
    hub = HookHub(list_pids=lambda: [])
    assert hub.status(7) is None
    assert hub.chat(7).model_dump() == {
        "connected": False,
        "proto": None,
        "last_seq": 0,
        "messages": [],
    }


async def _get(services, url):
    from httpx import ASGITransport, AsyncClient

    from services.api import build_app

    app = build_app(services=services)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(url)


async def test_chat_endpoint():
    hub = HookHub(list_pids=lambda: [])
    hub._ingest(1001, {"t": "hello", "v": 4})
    hub._ingest(1001, _msg(_chat(OTHER, 5, "路人", "毒蠍王刷了")))
    hub._ingest(1001, _msg(_chat(OTHER, 5, "路人", "在東邊")))
    r = await _get({"hook_hub": hub}, "/api/characters/1001/chat?after=1")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] and body["last_seq"] == 2
    assert [m["text"] for m in body["messages"]] == ["在東邊"]


async def test_chat_endpoint_without_hub():
    assert (await _get(None, "/api/characters/1001/chat")).status_code == 503
