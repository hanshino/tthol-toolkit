"""read_all_fields compat-layout (4-byte-shifted) remap.

In compat layout the game stores the HP/MP current/max pairs swapped
(struct_base+0 = max_HP, +4 = current_HP, +8 = max_MP, +12 = current_MP).
read_all_fields must remap those offsets when compat_mode is True so the
published vitals keep their correct meaning; the worker auto-selects compat
layout for some characters, so this path is reachable at runtime.
"""

from reader import read_all_fields

# Field names are data from knowledge.json (Chinese by design); comments stay
# English per project convention.
DISPLAY_FIELDS = [
    (0, "血量"),  # current HP
    (4, "最大血量"),  # max HP
    (8, "真氣"),  # current MP
    (12, "最大真氣"),  # max MP
    (-36, "等級"),  # level (unaffected by compat shift)
]


class FakePm:
    """Minimal pymem stand-in: read_int reads from an addr->value map."""

    def __init__(self, mem):
        self._mem = mem

    def read_int(self, addr):
        return self._mem[addr]


def test_normal_layout_reads_straight_offsets():
    mem = {0: 100, 4: 500, 8: 30, 12: 200, -36: 90}
    result = dict(read_all_fields(FakePm(mem), 0, DISPLAY_FIELDS, compat_mode=False))
    assert result == {"血量": 100, "最大血量": 500, "真氣": 30, "最大真氣": 200, "等級": 90}


def test_compat_layout_remaps_hp_mp_pairs():
    # Compat storage: max_HP@0, current_HP@4, max_MP@8, current_MP@12.
    mem = {0: 500, 4: 100, 8: 200, 12: 30, -36: 90}
    result = dict(read_all_fields(FakePm(mem), 0, DISPLAY_FIELDS, compat_mode=True))
    # Despite the swapped storage, each field keeps its correct meaning.
    assert result == {"血量": 100, "最大血量": 500, "真氣": 30, "最大真氣": 200, "等級": 90}


def test_compat_buffer_without_flag_is_swapped():
    # Regression guard: reading a compat-laid-out struct in normal mode yields
    # swapped HP/MP — the exact defect the compat_mode flag fixes.
    mem = {0: 500, 4: 100, 8: 200, 12: 30, -36: 90}
    result = dict(read_all_fields(FakePm(mem), 0, DISPLAY_FIELDS, compat_mode=False))
    assert result["血量"] == 500  # max leaks into current -> wrong
    assert result["最大血量"] == 100
    assert result["真氣"] == 200
    assert result["最大真氣"] == 30


def test_unmapped_field_read_error_is_tolerated():
    # A field whose offset is missing from memory raises KeyError inside read_int;
    # read_all_fields must degrade to "???" rather than propagate.
    mem = {0: 100, 4: 500, 8: 30, 12: 200}  # no -36 (等級)
    result = dict(read_all_fields(FakePm(mem), 0, DISPLAY_FIELDS, compat_mode=False))
    assert result["等級"] == "???"
