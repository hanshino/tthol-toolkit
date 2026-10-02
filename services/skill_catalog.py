"""Skill metadata for the 武學 tab: name, group, passive flag, cost, help, icon.

The worker only reads (magic id, level) pairs from the character; everything
else comes from the bundled tthol.sqlite, loaded once and kept in memory. Rows
are per (id, level) because the help text, MP cost and icon change with level.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from services._paths import bundled
from services.api_types import ItemStat, SkillInfo

log = logging.getLogger("tthol.skill_catalog")

DB_PATH = bundled("tthol.sqlite")

# magic.clan -> sect name. LOVE and GUILD are not sects but the couple and
# guild skill sets. A code missing here shows as the bare code.
CLAN_NAMES = {
    "CLASS_SKY": "天外天",
    "CLASS_BAD": "惡人谷",
    "CLASS_FOX": "火狐",
    "CLASS_FOX_SNOW": "雪狼",
    "CLASS_GOD": "神武",
    "CLASS_ISLE": "無名島",
    "CLASS_MONTO": "曼陀羅",
    "CLASS_MONTO_KYLIN": "麒麟",
    "CLASS_MAGIC": "天師",
    "CLASS_SHAULIN": "少林",
    "CLASS_FLOWER": "移花宮",
    "CLASS_LOVE": "戀人技能",
    "CLASS_GUILD": "公會技能",
}
# Novice / shared skills (好友, 休息, 群鴿飛書 ...) carry these or no clan at all.
_GENERAL_CLANS = frozenset({None, "CLASS_CHILD", "CLASS_NONE"})

GENERAL = ("general", "通用 · 生活")
BONUS = ("bonus", "屬性增加")
MERIDIAN = ("meridian", "經脈")

# Flat cap / stat bonuses spelled out in the help text of the current level.
# 嫁衣神功 writes "HP最大值增加", the 增加 skills "增加體力", 養精蓄銳 and
# 強筋健骨 "提昇X的上限值".
CAP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("體力上限", re.compile(r"(?:提昇體力的上限值|增加體力|HP最大值增加)(\d+)")),
    ("真氣上限", re.compile(r"(?:提昇真氣的上限值|增加真氣)(\d+)")),
    ("外功", re.compile(r"增加外功(\d+)")),
    ("內力", re.compile(r"增加內力(\d+)")),
    ("根骨", re.compile(r"增加根骨(\d+)")),
    ("技巧", re.compile(r"增加技巧(\d+)")),
    ("身法", re.compile(r"增加身法(\d+)")),
    ("玄學", re.compile(r"增加玄學(\d+)")),
    ("物攻", re.compile(r"增加物攻(\d+)")),
    ("內勁", re.compile(r"增加內勁(\d+)")),
    ("防禦", re.compile(r"增加防禦(\d+)")),
    ("護勁", re.compile(r"增加護勁(\d+)")),
    ("命中", re.compile(r"增加命中(\d+)")),
    ("閃躲", re.compile(r"增加閃躲(\d+)")),
)


@dataclass(frozen=True)
class _Row:
    name: str
    clan: str | None
    help: str
    mp_cost: int


def group_for(name: str, clan: str | None, meridian: bool) -> tuple[str, str]:
    """(key, label) of the group a skill is listed under."""
    if meridian:
        return MERIDIAN
    if clan is None and name.endswith("增加"):
        return BONUS
    if clan in _GENERAL_CLANS:
        return GENERAL
    return clan, CLAN_NAMES.get(clan, clan.removeprefix("CLASS_"))


def clean_help(text: str | None) -> str:
    """Drop the "最高等級，" prefix the max level's help starts with."""
    return (text or "").removeprefix("最高等級，").strip()


def is_passive(help_text: str, group: str) -> bool:
    return "自動使用" in help_text or group in (BONUS[0], MERIDIAN[0])


def skill_caps(skills: list[SkillInfo]) -> list[ItemStat]:
    """Flat bonuses the learned skills add, summed per stat, in CAP_RULES order."""
    totals: dict[str, int] = {}
    for skill in skills:
        for label, pattern in CAP_RULES:
            m = pattern.search(skill.description)
            if m:
                totals[label] = totals.get(label, 0) + int(m.group(1))
    return [ItemStat(label=label, value=totals[label]) for label, _ in CAP_RULES if label in totals]


def icon_path(magic_id: int, level: int) -> str:
    """Same-origin URL the UI loads a skill icon from; served via services.icon_cache."""
    return f"/api/skills/{magic_id}/icon?level={level}"


class SkillCatalog:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._rows: dict[tuple[int, int], _Row] | None = None
        self._max_level: dict[int, int] = {}
        self._meridians: frozenset[int] = frozenset()
        self._icons: dict[tuple[int, int], str] = {}
        self._lock = threading.Lock()

    def _load(self) -> None:
        try:
            con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            log.error("skill catalog unavailable: %s", exc, extra={"cat": "skills"})
            return
        try:
            con.text_factory = lambda b: b.decode("utf-8", errors="replace")
            rows = {
                (mid, lv): _Row(name or "", clan, clean_help(help_), mp or 0)
                for mid, lv, name, clan, help_, mp in con.execute(
                    "SELECT id, level, name, clan, help, spend_mp FROM magic"
                )
            }
            # magic_learn holds the learnable levels. magic also has rows past
            # them (monster-only 黯影 Lv51, a 1-MP 瞬獄烈斬 Lv80), so its own
            # MAX(level) overstates the cap; it is only the fallback.
            max_level: dict[int, int] = {}
            for (mid, lv), row in rows.items():
                if not row.help.startswith("怪物專用"):
                    max_level[mid] = max(max_level.get(mid, 0), lv)
            max_level.update(
                con.execute("SELECT magic_id, MAX(level) FROM magic_learn GROUP BY magic_id")
            )
            meridians = frozenset(
                mid for (mid,) in con.execute("SELECT magic_id FROM magic_meridians")
            )
            icons = {
                (mid, lv): url
                for mid, lv, url in con.execute("SELECT magic_id, level, url FROM magic_images")
            }
        except sqlite3.Error as exc:
            # Leave the cache empty so the next call retries.
            log.error("skill catalog load failed: %s", exc, extra={"cat": "skills"})
            return
        finally:
            con.close()
        self._rows, self._max_level, self._meridians, self._icons = (
            rows,
            max_level,
            meridians,
            icons,
        )

    def _ensure(self) -> dict[tuple[int, int], _Row]:
        with self._lock:
            if not self._rows:
                self._load()
            return self._rows or {}

    def describe(self, skills: list[tuple[int, int]]) -> list[SkillInfo]:
        """SkillInfo for each learned (magic id, level), in the given order."""
        rows = self._ensure()
        out = []
        for mid, lv in skills:
            row = rows.get((mid, lv)) or _Row("???", None, "", 0)
            key, label = group_for(row.name, row.clan, mid in self._meridians)
            out.append(
                SkillInfo(
                    magic_id=mid,
                    level=lv,
                    name=row.name,
                    # Never below the learned level, so a gap in the DB cannot
                    # show "12 / 10".
                    max_level=max(self._max_level.get(mid, lv), lv),
                    group=key,
                    group_label=label,
                    passive=is_passive(row.help, key),
                    mp_cost=row.mp_cost,
                    description=row.help,
                    icon_url=icon_path(mid, lv) if (mid, lv) in self._icons else None,
                )
            )
        return out

    def icon_url(self, magic_id: int, level: int) -> str | None:
        """Remote URL of a skill icon at a level, or None if it has none."""
        self._ensure()
        return self._icons.get((magic_id, level))


_catalog = SkillCatalog()


def describe(skills: list[tuple[int, int]]) -> list[SkillInfo]:
    return _catalog.describe(skills)


def icon_url(magic_id: int, level: int) -> str | None:
    return _catalog.icon_url(magic_id, level)
