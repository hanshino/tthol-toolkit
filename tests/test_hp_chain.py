"""Tests for reading HP straight from the engine charobject pointer chain.

The chain root (PLAYER_HP_CHAIN_BASE) resolves into the engine charobject; HP
is read with no memory scan. These tests use a fake process whose memory is a
plain {address: dword} map, exercising the chain walk + charobject field reads.
"""

import struct

from reader import (
    CHAR_HP_CUR_OFFSET,
    CHAR_HP_MAX_OFFSET,
    PLAYER_HP_CHAIN_BASE,
    PLAYER_HP_CHAIN_OFFSETS,
    read_hp_pair_from_chain,
)


class FakePm:
    """Minimal pymem stand-in backed by an {address: uint32} dict."""

    def __init__(self, mem):
        self.mem = mem

    def read_bytes(self, addr, n):
        assert n == 4
        return struct.pack("<I", self.mem.get(addr, 0) & 0xFFFFFFFF)

    def read_int(self, addr):
        return self.mem.get(addr, 0)


def _wire_chain(charobj):
    """Build a memory map whose chain from PLAYER_HP_CHAIN_BASE lands on charobj.
    Mirrors [[[BASE]+0x128]+0x68] with the last offset (current HP) excluded."""
    o1, o2 = PLAYER_HP_CHAIN_OFFSETS[0], PLAYER_HP_CHAIN_OFFSETS[1]
    p0, p1 = 0x01001000, 0x01002000
    return {
        PLAYER_HP_CHAIN_BASE: p0,
        p0 + o1: p1,
        p1 + o2: charobj,
    }


def test_read_hp_pair_ok():
    charobj = 0x01003000
    mem = _wire_chain(charobj)
    mem[charobj + CHAR_HP_CUR_OFFSET] = 100
    mem[charobj + CHAR_HP_MAX_OFFSET] = 120
    assert read_hp_pair_from_chain(FakePm(mem)) == (100, 120)


def test_read_hp_pair_null_first_link():
    assert read_hp_pair_from_chain(FakePm({PLAYER_HP_CHAIN_BASE: 0})) is None


def test_read_hp_pair_null_charobject():
    mem = _wire_chain(0)  # [P1+0x68] == 0 -> broken
    assert read_hp_pair_from_chain(FakePm(mem)) is None


def test_read_hp_pair_invalid_values():
    charobj = 0x01003000
    mem = _wire_chain(charobj)
    mem[charobj + CHAR_HP_CUR_OFFSET] = 0  # current HP must be >= 1
    mem[charobj + CHAR_HP_MAX_OFFSET] = 120
    assert read_hp_pair_from_chain(FakePm(mem)) is None
