"""Export the live character as a TTHOL1 string for genbu's stat simulator.

Contract: genbu docs/plans/2026-10-03-stat-sim-import-format.md (branch
feat/stat-sim-import). Only raw reads leave this module; genbu does every
derivation (rebirth points, splitting item stats into sockets and random
rolls, passive filtering), because its game data is newer and the formulas
live in one place.

    read_character(pm, hp_addr, compat_mode)  -> RawCharacter  (memory reads)
    build_payload(raw, app, at)               -> dict          (pure)
    encode(payload) / decode(code)            -> TTHOL1 string <-> dict
"""

from __future__ import annotations

import base64
import datetime
import functools
import json
import os
import zlib
from dataclasses import dataclass

import reader

PREFIX = "TTHOL1"
VERSION = 1
GENBU_URL = "https://genbu.hanshino.dev"
# Point the import link at another genbu (e.g. a local dev server) for testing.
GENBU_URL_ENV = "TTHOL_GENBU_URL"
IMPORT_PATH = "/tools/stat-sim#import="

# reader.EQUIP_SLOTS name -> genbu EquipSlot; genbu needs all ten keys.
SLOT_KEYS = {
    "CAP": "cap",
    "BODY": "body",
    "FOOT": "foot",
    "HAND_R": "right",  # two-handed weapons sit here too
    "HAND_L": "left",
    "WING": "wing",
    "HORSE": "horse",
    "ORNAMENT_1": "ornament1",
    "ORNAMENT_2": "ornament2",
    "ORNAMENT_3": "ornament3",
}
# Panel / bare attribute order (HP -96..-76 and -264..-244).
ATTR_KEYS = ("str", "pow", "vit", "agi", "dex", "wis")
# items column -> genbu StatKey where the names differ.
STAT_RENAMES = {"extra_def": "def", "magic_def": "mdef", "critical_hit": "critical"}
# genbu IMPORT_STAT_KEYS: any other key inside equipment.*.stats makes genbu
# reject the whole payload, so stats are whitelisted, not blacklisted.
IMPORT_STAT_KEYS = frozenset(
    {
        "hp", "mp", "str", "pow", "vit", "agi", "dex", "wis", "atk", "matk", "def",
        "mdef", "hit", "dodge", "critical", "uncanny_dodge", "attack_speed", "run_speed",
    }
)  # fmt: skip
# Weapon damage pairs go to the optional .damage block instead (labels follow
# the DB column order, see reader.ITEM_STAT_FIELDS).
DAMAGE_KEYS = {
    "damage_min": "min",
    "damage_max": "max",
    "pdamage_min": "pmin",
    "pdamage_max": "pmax",
}
# (panel key, offset from hp_addr). 最大血量 / 最大真氣 go through
# reader.read_all_fields so the compat layout's swap applies.
PANEL_FIELDS = (
    ("hp", 4),
    ("mp", 12),
    ("atk", 72),
    ("matk", 80),
    ("def", 84),
    ("mdef", 88),
    ("hit", 92),
    ("dodge", 96),
    ("critical", 100),
    ("uncanny_dodge", 104),
    ("attack_speed", 64),
    ("run_speed", 60),
    ("weight_cap", 28),
)
PANEL_ATTRS_OFFSET = -96
SECT_OFFSET = -196
LEVEL_OFFSET = -36
MAX_LEVEL = 300  # genbu's bound
# Base hairstyle item ids, used for gender when the doll head cannot be read.
# The female upper bound is assumed, not verified.
HAIR_MALE = range(29001, 29051)
HAIR_FEMALE = range(29051, 29101)
_FILL_VALUES = (reader.FREED_FILL, reader.UNINIT_FILL)


class NotReady(Exception):
    """The character cannot be exported yet (not located, or data not loaded)."""


@dataclass(frozen=True)
class RawCharacter:
    """Values as read from memory, before any renaming for genbu."""

    name: str
    sect: int
    level: int
    bare: list[int]
    remaining_points: int
    equipment: list[dict]  # reader.read_equipment_detail()
    skills: list[tuple[int, int]]
    panel_attrs: list[int]
    panel: dict[str, int]
    appearance: dict | None  # reader.read_appearance()
    hair_item: int
    hair_color: int
    statuses: list[tuple[int, str]]  # reader.read_active_statuses()


@functools.cache
def _knowledge():
    return reader.load_knowledge()


def _ints(pm, addr, count):
    return [pm.read_int(addr + 4 * i) for i in range(count)]


def read_character(pm, hp_addr, compat_mode=False) -> RawCharacter:
    """Read everything the export needs. Raises NotReady when hp_addr is not a
    live character or a read looks wrong (struct being freed or rebuilt)."""
    try:
        if not reader.is_char_object(pm, hp_addr):
            raise NotReady("lock is not a character object")
        name = reader.read_character_name(pm, hp_addr)
        sect = pm.read_int(hp_addr + SECT_OFFSET)
        level = pm.read_int(hp_addr + LEVEL_OFFSET)
        bare = reader.read_bare_attrs(pm, hp_addr)
        remaining = reader.read_remaining_points(pm, hp_addr)
        panel_attrs = _ints(pm, hp_addr + PANEL_ATTRS_OFFSET, len(ATTR_KEYS))
        panel_rows = reader.read_all_fields(
            pm, hp_addr, [(off, key) for key, off in PANEL_FIELDS], compat_mode
        )
        equipment = reader.read_equipment_detail(pm, hp_addr)
        skills = reader.read_skills(pm, hp_addr)
        appearance = reader.read_appearance(pm, hp_addr)
        hair_item = pm.read_int(hp_addr + reader.HAIR_ITEM_OFFSET)
        hair_color = pm.read_int(hp_addr + reader.HAIR_COLOR_OFFSET)
        statuses = reader.read_active_statuses(pm, hp_addr, _knowledge())
    except NotReady:
        raise
    except Exception as exc:
        raise NotReady(f"memory read failed: {exc}") from exc

    if not name:
        raise NotReady("no character name")
    if equipment is None or skills is None:
        raise NotReady("equipment or skills not readable")
    panel = dict(panel_rows)
    numbers = [sect, level, remaining, *bare, *panel_attrs, *panel.values()]
    if any(not isinstance(v, int) or v in _FILL_VALUES for v in numbers):
        raise NotReady("struct looks freed or uninitialised")
    if not 1 <= level <= MAX_LEVEL or min(bare) < 1 or remaining < 0:
        raise NotReady(f"implausible level / attributes (level {level}, bare {bare})")
    return RawCharacter(
        name=name,
        sect=sect,
        level=level,
        bare=bare,
        remaining_points=remaining,
        equipment=equipment,
        skills=skills,
        panel_attrs=panel_attrs,
        panel=panel,
        appearance=appearance,
        hair_item=hair_item,
        hair_color=hair_color,
        statuses=statuses,
    )


def _equip_entry(slot: dict) -> dict | None:
    if slot["item_id"] is None:
        return None
    stats = {}
    for column, value in slot["stats"].items():
        key = STAT_RENAMES.get(column, column)
        if value and key in IMPORT_STAT_KEYS:
            stats[key] = value
    entry = {
        "id": slot["item_id"],
        "plus": slot["plus"],
        "inlays": list(slot["inlays"]),
        "stats": stats,
    }
    damage = {DAMAGE_KEYS[c]: slot["stats"][c] for c in DAMAGE_KEYS if slot["stats"].get(c)}
    if damage:
        entry["damage"] = damage
    if slot["zhenjie"]:
        entry["zhenjie"] = slot["zhenjie"]
    entry["refineLeft"] = slot["refine_left"]
    return entry


def _appearance(raw: RawCharacter) -> dict | None:
    look = raw.appearance
    if look is not None:
        out = {
            "gender": look["gender"],
            "hairItem": look["hair_item"],
            "hairColor": look["hair_color"],
            "head": look["head"],
        }
        if look["cap"] is not None:
            out["cap"] = look["cap"]
        return out
    if raw.hair_item in HAIR_MALE:
        gender = "m"
    elif raw.hair_item in HAIR_FEMALE:
        gender = "f"
    else:
        return None
    color = raw.hair_color if 0 <= raw.hair_color <= reader.MAX_HAIR_COLOR else 0
    return {"gender": gender, "hairItem": raw.hair_item, "hairColor": color}


def build_payload(raw: RawCharacter, app: str, at: datetime.datetime) -> dict:
    """TTHOL1 v1 JSON for a character read by read_character()."""
    by_slot = {slot["slot"]: _equip_entry(slot) for slot in raw.equipment}
    panel: dict = {
        # genbu rejects panel attributes below 1; the panel is only a reference.
        "attributes": {k: v for k, v in zip(ATTR_KEYS, raw.panel_attrs) if v >= 1},
        **{key: raw.panel[key] for key, _off in PANEL_FIELDS},
    }
    payload = {
        "v": VERSION,
        "app": app,
        "at": at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "name": raw.name,
        "sect": raw.sect,
        "level": raw.level,
        "bare": dict(zip(ATTR_KEYS, raw.bare)),
        "remainingPoints": raw.remaining_points,
        "equipment": {key: by_slot.get(slot) for slot, key in SLOT_KEYS.items()},
        "skills": {str(skill_id): level for skill_id, level in raw.skills},
        "panel": panel,
        "statuses": {
            "buffs": [g for g, kind in raw.statuses if kind != "debuff"],
            "debuffs": [g for g, kind in raw.statuses if kind == "debuff"],
        },
    }
    appearance = _appearance(raw)
    if appearance is not None:
        payload["appearance"] = appearance
    return payload


def encode(payload: dict) -> str:
    """TTHOL1.<base64url(deflate-raw(UTF-8 JSON))>, without '=' padding."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = deflate.compress(raw) + deflate.flush()
    return f"{PREFIX}.{base64.urlsafe_b64encode(body).decode('ascii').rstrip('=')}"


def decode(code: str) -> dict:
    prefix, _, body = code.strip().partition(".")
    if prefix != PREFIX or not body:
        raise ValueError("not a TTHOL1 string")
    data = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    return json.loads(zlib.decompress(data, -15).decode("utf-8"))


def import_url(code: str) -> str:
    base = os.environ.get(GENBU_URL_ENV, "").strip() or GENBU_URL
    return base.rstrip("/") + IMPORT_PATH + code
