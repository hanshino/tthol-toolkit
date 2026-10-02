"""Tests for the skill metadata catalog behind CharacterDetail.skills."""

import sqlite3

from services.api_types import SkillInfo
from services.skill_catalog import SkillCatalog, group_for, skill_caps


def _db(tmp_path):
    path = tmp_path / "magic.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE magic (id, level, name, clan, help, spend_mp)")
    con.execute("CREATE TABLE magic_learn (magic_id, level)")
    con.execute("CREATE TABLE magic_meridians (magic_id)")
    con.execute("CREATE TABLE magic_images (magic_id, level, url)")
    rows = [
        (24, 6, "養精蓄銳", None, "自動使用，將提昇真氣的上限值105點。", None),
        (24, 10, "養精蓄銳", None, "最高等級，自動使用，將提昇真氣的上限值200點。", None),
        (270, 9, "黯影", "CLASS_ISLE", "最高等級，消耗63點真氣，施展忍法黯影", 63),
        (270, 51, "黯影", "CLASS_ISLE", "怪物專用，消耗75真氣", 75),
        (752, 15, "瞬獄烈斬", "CLASS_GOD", "消耗20點真氣", 20),
        (752, 80, "瞬獄烈斬", "CLASS_GOD", "消耗1點真氣", 1),
        (860, 1, "廉泉", None, "增加根骨1點", None),
        (1151, 2, "體力增加", None, "增加體力250點", None),
        (180, 4, "嫁衣神功一重", None, "則將受其反噬，HP最大值增加12000。", None),
        (2, 1, "休息", "CLASS_CHILD", "可以坐下休息", None),
        (900, 1, "棍法修行", "CLASS_SHAULIN", "自動使用，裝備棍", None),
        (901, 1, "未知技", "CLASS_NEW", "", None),
    ]
    con.executemany("INSERT INTO magic VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.executemany("INSERT INTO magic_learn VALUES (?, ?)", [(752, 14), (752, 15)])
    con.execute("INSERT INTO magic_meridians VALUES (860)")
    con.execute("INSERT INTO magic_images VALUES (24, 6, 'https://img/lzzm17.png')")
    con.commit()
    con.close()
    return SkillCatalog(path)


def _one(cat, mid, lv) -> SkillInfo:
    (info,) = cat.describe([(mid, lv)])
    return info


def test_describes_level_specific_help_and_cost(tmp_path):
    cat = _db(tmp_path)
    s = _one(cat, 270, 9)
    assert (s.name, s.group, s.group_label) == ("黯影", "CLASS_ISLE", "無名島")
    assert s.description == "消耗63點真氣，施展忍法黯影"  # 最高等級 prefix dropped
    assert s.mp_cost == 63
    assert s.passive is False


def test_max_level_skips_monster_rows_and_prefers_magic_learn(tmp_path):
    cat = _db(tmp_path)
    assert _one(cat, 270, 9).max_level == 9  # Lv51 is 怪物專用
    assert _one(cat, 752, 15).max_level == 15  # magic_learn wins over the Lv80 row
    assert _one(cat, 24, 6).max_level == 10


def test_groups(tmp_path):
    cat = _db(tmp_path)
    assert _one(cat, 860, 1).group == "meridian"
    assert _one(cat, 1151, 2).group == "bonus"
    assert _one(cat, 2, 1).group == "general"
    assert _one(cat, 900, 1).group_label == "少林"
    assert _one(cat, 901, 1).group_label == "NEW"  # unnamed clan shows its code
    assert group_for("刀修練", "CLASS_BAD", False) == ("CLASS_BAD", "惡人谷")


def test_passive_flags(tmp_path):
    cat = _db(tmp_path)
    assert _one(cat, 24, 6).passive
    assert _one(cat, 860, 1).passive  # meridian
    assert _one(cat, 1151, 2).passive  # stat bonus
    assert not _one(cat, 2, 1).passive


def test_icon_only_when_the_level_has_one(tmp_path):
    cat = _db(tmp_path)
    assert _one(cat, 24, 6).icon_url == "/api/skills/24/icon?level=6"
    assert _one(cat, 270, 9).icon_url is None
    assert cat.icon_url(24, 6) == "https://img/lzzm17.png"


def test_unknown_skill_keeps_its_level(tmp_path):
    s = _one(_db(tmp_path), 9999, 3)
    assert (s.name, s.max_level, s.group) == ("???", 3, "general")


def test_skill_caps_sum_help_text_bonuses(tmp_path):
    cat = _db(tmp_path)
    skills = cat.describe([(24, 6), (1151, 2), (180, 4), (860, 1), (270, 9)])
    caps = {c.label: c.value for c in skill_caps(skills)}
    assert caps == {"體力上限": 12250, "真氣上限": 105, "根骨": 1}
