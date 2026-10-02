"""Tests for reading the learned-skill list straight from the CCharObject.

hp_addr + 0xF4 holds the count, + 0xF8 / + 0xFC point to parallel u16 id and u8
level arrays. Built in a fake byte-addressable process like the inventory tests.
"""

import struct

import pytest

from reader import (
    CHAR_OBJ_HP_OFFSET,
    CHAR_OBJ_VTABLE,
    SKILL_COUNT_OFFSET,
    SKILL_IDS_OFFSET,
    SKILL_LEVELS_OFFSET,
    read_skills,
)
from tests.test_direct_inventory import FakePm

OBJ = 0x1C329238
HP = OBJ + CHAR_OBJ_HP_OFFSET
IDS = 0x1BC2CFF0
LEVELS = 0x1BC2DB60


def _char(pm, skills):
    pm.u32(OBJ, CHAR_OBJ_VTABLE)
    pm.u32(HP + SKILL_COUNT_OFFSET, len(skills))
    pm.u32(HP + SKILL_IDS_OFFSET, IDS)
    pm.u32(HP + SKILL_LEVELS_OFFSET, LEVELS)
    pm.write(IDS, b"".join(struct.pack("<H", i) for i, _ in skills))
    pm.write(LEVELS, bytes(lv for _, lv in skills))


def test_reads_parallel_id_and_level_arrays():
    pm = FakePm()
    skills = [(1, 1), (24, 6), (186, 11), (649, 1)]
    _char(pm, skills)
    assert read_skills(pm, HP) == skills


def test_rejects_hp_addr_outside_a_char_object():
    pm = FakePm()
    _char(pm, [(24, 6)])
    pm.u32(OBJ, 0x12345678)
    assert read_skills(pm, HP) is None


def test_empty_list_reads_no_pointers():
    pm = FakePm()
    _char(pm, [])
    pm.u32(HP + SKILL_IDS_OFFSET, 0)
    assert read_skills(pm, HP) == []


@pytest.mark.parametrize("count", [-1, 100000])
def test_implausible_count_raises(count):
    pm = FakePm()
    _char(pm, [(24, 6)])
    pm.u32(HP + SKILL_COUNT_OFFSET, count)
    with pytest.raises(ValueError):
        read_skills(pm, HP)


def test_null_array_pointer_raises():
    pm = FakePm()
    _char(pm, [(24, 6)])
    pm.u32(HP + SKILL_LEVELS_OFFSET, 0)
    with pytest.raises(ValueError):
        read_skills(pm, HP)
