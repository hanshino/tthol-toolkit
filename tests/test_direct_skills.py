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


def _worker(got):
    from services.worker import ReaderWorker

    return ReaderWorker(
        pid=1,
        on_state=lambda _s: None,
        on_stats=lambda _s: None,
        on_inventory=lambda _i: None,
        on_warehouse=lambda _w: None,
        on_error=lambda _m, **_kw: None,
        on_skills=got.append,
    )


def test_auto_read_pushes_skills():
    pm = FakePm()
    _char(pm, [(24, 6), (186, 11)])
    got = []
    _worker(got)._auto_read_items(pm, HP)
    assert got == [[(24, 6), (186, 11)]]


def test_auto_read_skips_unreadable_skills():
    pm = FakePm()
    _char(pm, [(24, 6)])
    pm.u32(HP + SKILL_COUNT_OFFSET, -1)
    got = []
    _worker(got)._auto_read_items(pm, HP)
    assert got == []


def test_session_describes_skills_once_per_change(monkeypatch):
    from services import char_session
    from services.api_types import SkillInfo

    calls = []

    def describe(skills):
        calls.append(skills)
        return [
            SkillInfo(
                magic_id=mid,
                level=lv,
                name="養精蓄銳",
                max_level=10,
                group="general",
                group_label="通用 · 生活",
                passive=True,
                description="將提昇真氣的上限值105點。",
            )
            for mid, lv in skills
        ]

    monkeypatch.setattr(char_session.skill_catalog, "describe", describe)
    s = char_session.CharSession(pid=1)
    assert s.detail().skills is None
    s._on_skills([(24, 6)])
    s._on_skills([(24, 6)])
    assert calls == [[(24, 6)]]
    detail = s.detail()
    assert [(k.magic_id, k.level) for k in detail.skills] == [(24, 6)]
    assert [(c.label, c.value) for c in detail.skill_caps] == [("真氣上限", 105)]
