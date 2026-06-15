"""Regression: verify_structure must reject struct-shaped memory that has no
valid character name at NAME_OFFSET.

A second game client that wasn't logged in yet caused read_hp_from_player_chain
to return a degenerate value (1); locate_character then scanned for the int32
`1` (the most common value in memory) and locked onto a static region that
passed every numeric check with a perfect score but had an empty name. Because
it never failed validation, the worker's re-locate self-healing never fired and
the UI stayed stuck on the "(連線中)" placeholder. Requiring a real Big5 name
distinguishes a true character struct from such false positives.
"""

from reader import verify_structure, verify_structure_shifted

# Field names come from knowledge.json (Chinese by design); comments stay English.
_FIELDS: dict = {}  # verify_* only uses fixed offsets, not this map


class FakePm:
    """pymem stand-in: read_int reads offsets from a dict, read_bytes returns
    the name blob (verify_* only reads bytes for the name at NAME_OFFSET)."""

    def __init__(self, ints, name=b""):
        self._ints = ints
        self._name = name

    def read_int(self, addr):
        return self._ints.get(addr, 0)

    def read_bytes(self, addr, size):
        return (self._name + b"\x00" * size)[:size]


# A struct whose numeric fields all pass the hard constraints and trip no soft
# penalty / sequential-pattern heuristic — so its score hinges solely on name.
_NORMAL_INTS = {
    0: 287,
    4: 287,
    8: 100,
    12: 100,
    24: 50,
    28: 1000,
    -36: 99,
    -96: 10,
    -88: 10,
    -80: 10,
    44: 10,
    416: 5,
    420: 5,
}
# Shifted layout: max@0, current@4, max@8, current@12.
_SHIFTED_INTS = {
    0: 287,
    4: 287,
    8: 100,
    12: 100,
    24: 50,
    28: 1000,
    -36: 99,
    -96: 10,
    -88: 10,
    -80: 10,
    44: 10,
    416: 5,
    420: 5,
}


def test_verify_structure_rejects_struct_without_name():
    pm = FakePm(_NORMAL_INTS, name=b"")  # empty name -> not a real character
    assert verify_structure(pm, 0, _FIELDS) == 0.0


def test_verify_structure_accepts_struct_with_valid_big5_name():
    pm = FakePm(_NORMAL_INTS, name="奧黑比呂".encode("big5"))
    assert verify_structure(pm, 0, _FIELDS) >= 0.8


def test_verify_structure_rejects_non_big5_name_bytes():
    # Bytes that are not a valid Big5 lead/trail pair (ASCII-range garbage).
    pm = FakePm(_NORMAL_INTS, name=b"\x01\x02\x03\x04")
    assert verify_structure(pm, 0, _FIELDS) == 0.0


def test_verify_structure_shifted_rejects_struct_without_name():
    pm = FakePm(_SHIFTED_INTS, name=b"")
    assert verify_structure_shifted(pm, 0, _FIELDS) == 0.0


def test_verify_structure_shifted_accepts_struct_with_valid_big5_name():
    pm = FakePm(_SHIFTED_INTS, name="晨曦破空".encode("big5"))
    assert verify_structure_shifted(pm, 0, _FIELDS) >= 0.8


# Pure-numeric (and other pure-ASCII) character names are legal in-game. They
# are single-byte in Big5, so the old "first byte is a Big5 lead byte + whole
# name is an even number of bytes" rule rejected them, leaving such characters
# permanently stuck on the "(連線中)" placeholder. The name check must admit
# alphanumeric names while still rejecting empty / control-byte garbage.
def test_verify_structure_accepts_pure_numeric_name():
    pm = FakePm(_NORMAL_INTS, name=b"12345")  # odd byte length, ASCII digits
    assert verify_structure(pm, 0, _FIELDS) >= 0.8


def test_verify_structure_accepts_even_length_numeric_name():
    pm = FakePm(_NORMAL_INTS, name=b"1234")  # even length, ASCII-leading
    assert verify_structure(pm, 0, _FIELDS) >= 0.8


def test_verify_structure_accepts_alphanumeric_name():
    pm = FakePm(_NORMAL_INTS, name=b"Player1")
    assert verify_structure(pm, 0, _FIELDS) >= 0.8


def test_verify_structure_accepts_mixed_ascii_and_big5_name():
    pm = FakePm(_NORMAL_INTS, name="6之".encode("big5"))
    assert verify_structure(pm, 0, _FIELDS) >= 0.8


def test_verify_structure_accepts_name_with_printable_punctuation():
    # Underscores / printable punctuation appear in real names. The previous
    # rule accepted any clean Big5 decode, so the fix must not newly reject
    # these (that would just trade one stuck-on-"(連線中)" bug for another).
    pm = FakePm(_NORMAL_INTS, name=b"Player_1")
    assert verify_structure(pm, 0, _FIELDS) >= 0.8


def test_verify_structure_shifted_accepts_pure_numeric_name():
    pm = FakePm(_SHIFTED_INTS, name=b"7788")
    assert verify_structure_shifted(pm, 0, _FIELDS) >= 0.8
