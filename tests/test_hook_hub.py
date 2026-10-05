"""Tests for the hook pipe chat reader (services.hook_hub). Events are fed
straight into the hub, no pipe involved."""

import struct

import pytest

from services import hook_hub
from services.hook_hub import HookHub, decode_chat, decode_system

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


# --- templates: world shouts (ids in the text) and system lines (0xFD) ---

STRINGS = {
    6035: "%s 說> 開運到，歡喜開 %s 禮得 %s 好寶。",
    6031: "[終戰公告]%sPK賽已經結束，這場全民武鬥大會PK賽由 %s 方獲得勝利",
    60077: "家族成員%s上線了!",
    6014: "%s (%s) 大聲說> %s",
    90001: "得到 %d%% 經驗",
}


def _system(template_id: int, *args: str) -> bytes:
    return (
        b"\xfd"
        + template_id.to_bytes(2, "little")
        + b"\0".join(a.encode("cp950") for a in args)
        + b"\0"
    )


def test_decode_system_reads_template_and_args():
    # Captured 2026-10-05: 家族成員阿克婭上線了!
    assert decode_system(bytes.fromhex("fdadeaaafca74ad4d500")) == (60077, ["阿克婭"])
    assert decode_system(_system(6031, "東", "西")) == (6031, ["東", "西"])
    assert decode_system(b"\xfd\x01") is None


def test_fill_matches_specifiers_to_args():
    assert hook_hub.fill("家族成員%s上線了!", ["阿克婭"]) == "家族成員阿克婭上線了!"
    assert hook_hub.fill("得到 %d%% 經驗", ["30"]) == "得到 30% 經驗"
    assert hook_hub.fill("家族成員%s上線了!", []) is None


def test_render_shout_fills_the_template_without_repeating_the_name():
    text = hook_hub.render_shout("6035 天芯 聖龍靴毛胚 蔚藍聖龍鞋", "天芯", STRINGS)
    assert text == "開運到，歡喜開 聖龍靴毛胚 禮得 蔚藍聖龍鞋 好寶。"


def test_render_shout_keeps_spaces_in_the_last_arg():
    text = hook_hub.render_shout("6014 路人 成都市集 收 4J 刀劍", "路人", STRINGS)
    assert text == "路人 (成都市集) 大聲說> 收 4J 刀劍"


@pytest.mark.parametrize(
    "text",
    [
        "收4J刀劍，價錢好談",  # a plain shout
        "6035 天芯 聖龍靴毛胚",  # too few args for the template
        "7777 天芯",  # unknown id
    ],
)
def test_render_shout_leaves_other_text_alone(text):
    assert hook_hub.render_shout(text, "天芯", STRINGS) == text


def test_render_system_without_a_template_shows_the_id():
    assert hook_hub.render_system(60077, ["阿克婭"], {}) == "#60077 阿克婭"


def test_hub_fills_templates_when_the_log_is_read():
    calls = []

    def strings(pid):
        calls.append(pid)
        return STRINGS

    hub = HookHub(list_pids=lambda: [], strings=strings)
    hub._ingest(5, _msg(bytes.fromhex("fdadeaaafca74ad4d500")))
    hub._ingest(5, _msg(_chat(b"\0" * 10, 200, "天芯", "6035 天芯 聖龍靴毛胚 蔚藍聖龍鞋")))
    hub._ingest(5, _msg(_chat(OTHER, 1, "路人", "6035 不是廣播")))
    msgs = hub.chat(5).messages
    assert [(m.channel, m.name, m.text) for m in msgs] == [
        ("system", "", "家族成員阿克婭上線了!"),
        ("shout", "天芯", "開運到，歡喜開 聖龍靴毛胚 禮得 蔚藍聖龍鞋 好寶。"),
        ("normal", "路人", "6035 不是廣播"),
    ]
    hub.chat(5)
    assert calls == [5]  # read once per client


def test_hub_retries_the_template_table_later(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(hook_hub.time, "monotonic", lambda: clock[0])
    table = [None]
    hub = HookHub(list_pids=lambda: [], strings=lambda pid: table[0])
    hub._ingest(5, _msg(_system(60077, "阿克婭")))
    assert hub.chat(5).messages[0].text == "#60077 阿克婭"  # character not located yet
    table[0] = STRINGS
    assert hub.chat(5).messages[0].text == "#60077 阿克婭"  # not retried right away
    clock[0] += hook_hub.STRINGS_RETRY
    assert hub.chat(5).messages[0].text == "家族成員阿克婭上線了!"


def test_read_string_table_walks_the_tree():
    from reader import STRING_TABLE_PTR, read_string_table
    from tests.test_nearby import FakePm

    pm = FakePm()
    head, a, b, c = 0x02000000, 0x02000100, 0x02000200, 0x02000300
    pm.u32(STRING_TABLE_PTR + 4, head)
    pm.u32(STRING_TABLE_PTR + 8, 3)
    pm.write(head, struct.pack("<IIIiI", a, b, c, 0, 0) + b"\x01\x01")  # head: isnil
    pm.u32(head + 4, b)  # root

    def node(addr, left, right, key, text_addr, text):
        pm.write(addr, struct.pack("<IIIiI", left, 0, right, key, text_addr) + b"\x01\x00")
        pm.write(text_addr, text.encode("cp950") + b"\0stale")

    node(b, a, c, 6035, 0x02100000, STRINGS[6035])
    node(a, head, head, 6031, 0x02100100, STRINGS[6031])
    node(c, head, head, 60077, 0x02100200, STRINGS[60077])
    assert read_string_table(pm) == {k: STRINGS[k] for k in (6031, 6035, 60077)}


def _vitals(key: bytes, hp: int, mp: int) -> bytes:
    import struct

    return b"\x06" + key + struct.pack("<II", hp, mp)


def test_decode_vitals():
    from services.hook_hub import decode_vitals

    assert decode_vitals(_vitals(ME, 48877, 6170)) == (ME, 48877, 6170)
    assert decode_vitals(b"\x06" + b"\0" * 5) is None
    assert decode_vitals(_chat(OTHER, 1, "a", "b")) is None


def test_hub_hands_own_vitals_to_listeners_and_keeps_nothing():
    hub = HookHub(list_pids=lambda: [])
    got = []
    hub.add_vitals_listener(lambda pid, hp, mp: got.append((pid, hp, mp)))
    hub._ingest(5, {"t": "hello", "v": 4})
    hub._ingest(5, _msg(_vitals(ME, 30000, 100)))
    hub._ingest(5, _msg(_vitals(OTHER, 1, 1)))  # not ours
    hub._ingest(5, _msg(_vitals(ME, 1, 1), self_key=None))  # own key unknown: skip
    assert got == [(5, 30000, 100)]
    assert hub.chat(5).messages == []


def test_vitals_listener_errors_do_not_break_the_reader():
    hub = HookHub(list_pids=lambda: [])

    def boom(pid, hp, mp):
        raise RuntimeError("x")

    hub.add_vitals_listener(boom)
    hub._ingest(5, _msg(_vitals(ME, 1, 1)))  # must not raise
