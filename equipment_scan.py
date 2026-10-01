"""PoC: read equipped items from the on-map character objects.

See docs/plans/2026-10-01-equipment-reading-investigation.md. Every character
drawn on the map has an object whose first dword is a fixed vtable; the object
holds 11 equipment-slot pointers, and each pointed-to item instance has the
item id at +5. Read-only, like the rest of this project.

Usage:
    uv run equipment_scan.py            # list every character object found
    uv run equipment_scan.py --self     # also mark which one is the player
"""

import sqlite3
import struct
import sys
import time

import pymem

from reader import (
    CHAR_OBJ_VTABLE,
    get_memory_regions,
    load_knowledge,
    locate_character,
    read_character_name,
    read_hp_from_player_chain,
)
from services._paths import bundled

ACTION_OFFSET = 0x088
NAME_OFFSET = 0x1E4
TITLE_OFFSET = 0x1F1
GUILD_OFFSET = 0x221
STRING_MAX = 32
ITEM_ID_OFFSET = 5

EQUIP_SLOTS = [
    (0x374, "HEAD?"),
    (0x378, "CAP"),
    (0x37C, "BODY"),
    (0x380, "FOOT"),
    (0x384, "WING"),
    (0x388, "HORSE"),
    (0x38C, "ORNAMENT_1"),
    (0x390, "ORNAMENT_2"),
    (0x394, "ORNAMENT_3"),
    (0x398, "HAND_L"),
    (0x39C, "HAND_R"),
]


def read_cstr(pm, addr):
    """Null-terminated Big5 string, or None when unreadable / not terminated."""
    try:
        raw = pm.read_bytes(addr, STRING_MAX)
    except Exception:
        return None
    end = raw.find(b"\x00")
    if end == -1:
        return None
    try:
        return raw[:end].decode("big5")
    except UnicodeDecodeError:
        return None


def is_valid_name(name):
    return bool(name) and all(ord(ch) > 0x20 and ord(ch) != 0x7F for ch in name)


def is_valid_action(action):
    return bool(action) and action.isascii() and action.isprintable()


def scan_char_objects(pm):
    """Return addresses of 4-byte-aligned dwords equal to the vtable."""
    needle = struct.pack("<I", CHAR_OBJ_VTABLE)
    hits = []
    for base, size in get_memory_regions(pm.process_handle):
        try:
            buf = pm.read_bytes(base, size)
        except Exception:
            continue
        pos = buf.find(needle)
        while pos != -1:
            if pos % 4 == 0:
                hits.append(base + pos)
            pos = buf.find(needle, pos + 1)
    return hits


def read_equipment(pm, obj):
    slots = []
    for off, label in EQUIP_SLOTS:
        try:
            ptr = struct.unpack("<I", pm.read_bytes(obj + off, 4))[0]
            item_id = pm.read_int(ptr + ITEM_ID_OFFSET) if ptr else None
        except Exception:
            ptr, item_id = None, None
        slots.append((off, label, ptr, item_id))
    return slots


def load_items():
    conn = sqlite3.connect(str(bundled("tthol.sqlite")))
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    rows = conn.execute("SELECT id, name, flag_equip, equip_slot FROM items").fetchall()
    conn.close()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def find_self_name(pm):
    hp = read_hp_from_player_chain(pm)
    if hp is None:
        return None
    hp_addr = locate_character(pm, hp, load_knowledge())
    if isinstance(hp_addr, tuple):
        hp_addr = hp_addr[0]
    return read_character_name(pm, hp_addr) if hp_addr else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pm = pymem.Pymem("tthola.dat")

    self_name = find_self_name(pm) if "--self" in sys.argv else None
    if "--self" in sys.argv:
        print(f"Self name (from HP struct): {self_name!r}")

    t0 = time.perf_counter()
    hits = scan_char_objects(pm)
    elapsed = time.perf_counter() - t0
    print(f"vtable hits: {len(hits)} in {elapsed:.3f}s")

    items = load_items()
    valid = 0
    for obj in hits:
        name = read_cstr(pm, obj + NAME_OFFSET)
        action = read_cstr(pm, obj + ACTION_OFFSET)
        if not (is_valid_name(name) and is_valid_action(action)):
            print(f"  [skip] 0x{obj:08X} name={name!r} action={action!r}")
            continue
        valid += 1
        mark = "  <== SELF" if self_name and name == self_name else ""
        title = read_cstr(pm, obj + TITLE_OFFSET)
        guild = read_cstr(pm, obj + GUILD_OFFSET)
        print(f"\n0x{obj:08X}  {name}  action={action}  title={title!r}  guild={guild!r}{mark}")
        for off, label, ptr, item_id in read_equipment(pm, obj):
            if not ptr:
                print(f"  +0x{off:03X} {label:<10} -")
                continue
            name_, flag, slot = items.get(item_id, ("<unknown>", None, None))
            flag_s = f"0x{flag & 0xFFFFFFFF:08X}" if isinstance(flag, int) else flag
            print(
                f"  +0x{off:03X} {label:<10} {item_id:>6} {name_}  flag_equip={flag_s} equip_slot={slot}"
            )
    print(f"\nvalid character objects: {valid}/{len(hits)}")


if __name__ == "__main__":
    main()
