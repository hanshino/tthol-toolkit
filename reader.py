"""
Tthol character status and inventory reader.
Scans memory to locate character struct, then displays stats and inventory.
"""

import pymem
import ctypes
import ctypes.wintypes
import struct
import json
import sqlite3
import sys
import time

from services._paths import bundled


# ============================================================
# Stable pointer chain (updated after game patches via find_stable_chain.py)
# Root 0x00804B90 is tthola.dat's master singleton. There is no ASLR, so it is
# fixed for every run *of a given build* -- but a game patch relocates it, which
# is how it silently became 0x007F7810 -> 0x00804B90 on the 2026-08-07 update.
# The offsets below survived that patch untouched; only the root moved.
# Re-derive with /tthol-update-scan when detail.chain_walk shows a first hop
# that is not a heap pointer.
# The chain resolves into the *engine charobject* (the authoritative object the
# server updates), NOT the flat display struct that locate_character() scans for
# -- they are different heap allocations. The charobject reliably holds the vital
# stats: current HP @ +0x140, max HP @ +0x130. So HP can be read directly from
# the chain with no memory scan (see read_hp_pair_from_chain). Level / name /
# attributes / coords are NOT in the charobject; those still come from the flat
# struct via locate_character().
# CE notation (current HP): [[[[0x00804B90]+0x128]+0x68]+0x140]
# ============================================================
PLAYER_HP_CHAIN_BASE = 0x00804B90
PLAYER_HP_CHAIN_OFFSETS = [0x128, 0x68, 0x140]

# Vital-stat offsets inside the engine charobject (= the chain resolved without
# its final offset). Verified live 2026-06: the sub-struct is laid out as
# [guard][max][0][0][guard][current], with max/current 0x10 apart.
CHAR_HP_MAX_OFFSET = 0x130
CHAR_HP_CUR_OFFSET = 0x140


def _resolve_chain(pm, offsets):
    """Walk the static pointer chain from PLAYER_HP_CHAIN_BASE and return the
    final address, or None if any link is null / outside the 32-bit user range."""
    addr = PLAYER_HP_CHAIN_BASE
    for off in offsets:
        ptr = struct.unpack("<I", pm.read_bytes(addr, 4))[0]
        if ptr == 0 or ptr > 0x7FFFFFFF:
            return None
        addr = ptr + off
    return addr


def read_hp_from_player_chain(pm):
    """Read current HP value via the stable cross-restart pointer chain.

    Returns HP as int, or None if the chain is broken.
    """
    try:
        addr = _resolve_chain(pm, PLAYER_HP_CHAIN_OFFSETS)
        if addr is None:
            return None
        hp = pm.read_int(addr)
        if hp <= 0 or hp > 500000:
            return None
        return hp
    except Exception:
        return None


def read_hp_pair_from_chain(pm):
    """Read (current_hp, max_hp) straight from the engine charobject via the
    stable pointer chain -- no memory scan, stable across restarts and map
    changes. The charobject base is the chain resolved without its final offset;
    current HP sits at +CHAR_HP_CUR_OFFSET, max HP at +CHAR_HP_MAX_OFFSET.

    Returns (current, max), or None if the chain is broken or the values look
    invalid. These are the authoritative engine values -- prefer them over the
    flat display struct's HP copy, which is located by scanning.
    """
    try:
        charobj_slot = _resolve_chain(pm, PLAYER_HP_CHAIN_OFFSETS[:-1])
        if charobj_slot is None:
            return None
        charobj = struct.unpack("<I", pm.read_bytes(charobj_slot, 4))[0]
        if charobj == 0 or charobj > 0x7FFFFFFF:
            return None
        cur = pm.read_int(charobj + CHAR_HP_CUR_OFFSET)
        mx = pm.read_int(charobj + CHAR_HP_MAX_OFFSET)
        if not (1 <= cur <= 999999 and 1 <= mx <= 999999):
            return None
        return cur, mx
    except Exception:
        return None


# ============================================================
# 記憶體掃描基礎設施
# ============================================================
class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.wintypes.DWORD),
        ("Protect", ctypes.wintypes.DWORD),
        ("Type", ctypes.wintypes.DWORD),
    ]


MEM_COMMIT = 0x1000
READABLE_PAGES = (0x04, 0x08, 0x40, 0x80)


def get_memory_regions(process_handle):
    regions = []
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while address < 0x7FFFFFFF:
        result = ctypes.windll.kernel32.VirtualQueryEx(
            process_handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if result == 0:
            break
        base = mbi.BaseAddress or 0
        region_size = mbi.RegionSize
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE_PAGES:
            regions.append((base, region_size))
        address = base + region_size
        if region_size == 0:
            break
    return regions


# ============================================================
# 知識庫
# ============================================================
def load_knowledge():
    path = bundled("knowledge.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_display_fields(knowledge):
    """取得要顯示的欄位（過濾掉未知欄位）"""
    fields = knowledge["character_structure"]["fields"]
    result = []
    for offset_str, info in fields.items():
        if info["name"] == "未知":
            continue
        result.append((int(offset_str), info["name"]))
    result.sort(key=lambda x: x[0])
    return result


def parse_filters(filter_args):
    """Parse list of 'field=value' strings into {field_name: int_value} dict.
    Exits with error message if value is not a valid integer.
    """
    result = {}
    for item in filter_args:
        if "=" not in item:
            print(f"[X] Invalid --filter format '{item}', expected field=value")
            raise SystemExit(1)
        name, raw = item.split("=", 1)
        if not name:
            print(f"[X] Invalid --filter format '{item}', field name cannot be empty")
            raise SystemExit(1)
        try:
            result[name] = int(raw)
        except ValueError:
            print(f"[X] --filter value must be integer, got '{raw}' for field '{name}'")
            raise SystemExit(1)
    return result


def resolve_filters(filters, knowledge):
    """Resolve {field_name: value} to {offset: value} using knowledge.json.
    Exits with error if a field name is not found in the knowledge base.
    """
    fields = knowledge["character_structure"]["fields"]
    name_to_offset = {
        info["name"]: int(offset_str)
        for offset_str, info in fields.items()
        if info["name"] != "未知"
    }
    result = {}
    for name, value in filters.items():
        if name not in name_to_offset:
            known = ", ".join(sorted(name_to_offset.keys()))
            print(f"[X] Unknown field '{name}'. Known fields: {known}")
            raise SystemExit(1)
        result[name_to_offset[name]] = value
    return result


# ============================================================
# 定位角色結構
# ============================================================
# Real character structs live on the heap; static/module data sits below this
# floor. A candidate below it is a struct-shaped false positive: a second client
# that had not logged in once locked dead static memory (0x00A3BE14) that scored
# a perfect 1.0 and so never re-located. 32-bit heap spans up to 0x7FFFFFFF.
HEAP_MIN_ADDR = 0x10000000

# Character name: null-terminated Big5 string at NAME_OFFSET from the HP base.
NAME_OFFSET = -228
NAME_MAX_BYTES = 32


def _is_name_char(ch: str) -> bool:
    """True for a character that can legitimately appear in a character name.

    Names are CJK ideographs and/or ASCII alphanumerics, occasionally with
    printable punctuation (e.g. an underscore). Rather than allow-list those
    categories -- which would newly reject the punctuation the previous rule
    accepted -- this rejects only what marks struct-shaped garbage: C0/C1
    control bytes, space, and DEL. Everything else that decoded cleanly as Big5
    is treated as a name character.
    """
    o = ord(ch)
    return o > 0x20 and o != 0x7F and not (0x80 <= o <= 0x9F)


def _has_valid_character_name(pm, struct_base):
    """True when a plausible character name sits at NAME_OFFSET.

    A genuine character always has a name there; struct-shaped garbage (static
    data, freed heap) does not. Used as a hard constraint in verify_structure to
    reject false positives that satisfy the numeric checks but are not real
    characters.

    The name must be null-terminated within the 32-byte field, decode cleanly as
    Big5, and consist solely of name characters (CJK ideographs or ASCII
    alphanumerics). Admitting ASCII lets pure-numeric and English names through
    -- they are single-byte in Big5 and were wrongly rejected by the previous
    "first byte must be a Big5 lead byte / name must be an even byte count" rule,
    which left such characters stuck forever on the "(連線中)" placeholder. The
    all-name-char + null-terminated requirement still rejects empty names and
    control-byte / random-heap garbage.
    """
    try:
        raw = pm.read_bytes(struct_base + NAME_OFFSET, NAME_MAX_BYTES)
    except Exception:
        return False
    # A real name is null-terminated within the 32-byte field (padded after).
    # Garbage heap with no terminator near the start is not a character name.
    end = raw.find(b"\x00")
    if end < 1:
        return False
    raw = raw[:end]
    try:
        name = raw.decode("big5")
    except Exception:
        return False
    return all(_is_name_char(ch) for ch in name)


def locate_character(pm, hp_value, knowledge, offset_filters=None, compat_mode=False):
    """Scan memory for HP value, return best candidate with highest score.

    offset_filters: dict of {offset: expected_int_value} — candidate rejected if any mismatch.
    compat_mode: when True, also attempts a second scan with a 4-byte-shifted struct layout.
                 Some characters (observed on unequipped chars) have their struct stored with
                 max_HP at offset 0 and current_HP at offset +4 instead of the normal order.
                 In that case locate_character returns struct_base (= found_addr - 4) so that
                 all knowledge.json offsets are applied correctly from struct_base.
    """
    if offset_filters is None:
        offset_filters = {}
    regions = get_memory_regions(pm.process_handle)
    target_bytes = struct.pack("<i", hp_value)
    fields = knowledge["character_structure"]["fields"]

    candidates = []
    for base, size in regions:
        try:
            buffer = pm.read_bytes(base, size)
            offset = 0
            while True:
                pos = buffer.find(target_bytes, offset)
                if pos == -1:
                    break
                if pos % 4 == 0 and base + pos >= HEAP_MIN_ADDR:
                    addr = base + pos
                    score = verify_structure(pm, addr, fields)
                    if score >= 0.8:
                        # Apply user-supplied filters; treat read errors as filter miss
                        try:
                            passes = all(
                                pm.read_int(addr + off) == val
                                for off, val in offset_filters.items()
                            )
                        except Exception:
                            passes = False
                        if passes:
                            candidates.append((addr, score))
                offset = pos + 1
        except Exception:
            pass

    # Compat fallback: scan for hp_value at offset +4 from struct_base (shifted layout).
    # Only attempted when compat_mode is True and normal scan found no valid candidates.
    if compat_mode and not candidates:
        for base, size in regions:
            try:
                buffer = pm.read_bytes(base, size)
                offset = 0
                while True:
                    pos = buffer.find(target_bytes, offset)
                    if pos == -1:
                        break
                    # hp_value is at addr = struct_base + 4 (shifted by 4 bytes)
                    if pos % 4 == 0 and pos >= 4 and base + pos - 4 >= HEAP_MIN_ADDR:
                        struct_base = base + pos - 4
                        score = verify_structure_shifted(pm, struct_base, fields)
                        if score >= 0.8:
                            try:
                                passes = all(
                                    pm.read_int(struct_base + off) == val
                                    for off, val in offset_filters.items()
                                )
                            except Exception:
                                passes = False
                            if passes:
                                candidates.append((struct_base, score))
                    offset = pos + 1
            except Exception:
                pass

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def verify_structure(pm, hp_addr, fields, skip_seq_check=False):
    """Validate if address matches character struct with strict checks.

    skip_seq_check: unused, kept for API compatibility.
    """
    try:
        # Read key fields
        hp = pm.read_int(hp_addr + 0)
        hp_max = pm.read_int(hp_addr + 4)
        mp = pm.read_int(hp_addr + 8)
        mp_max = pm.read_int(hp_addr + 12)
        weight = pm.read_int(hp_addr + 24)
        weight_max = pm.read_int(hp_addr + 28)
        level = pm.read_int(hp_addr - 36)

        # Hard constraints (must pass)
        if not (1 <= hp <= hp_max <= 999999):
            return 0.0
        if not (0 <= mp <= mp_max <= 999999):
            return 0.0
        if not (0 <= weight <= weight_max <= 999999):
            return 0.0
        if not (1 <= level <= 200):
            return 0.0
        # A real character has a Big5 name here; struct-shaped garbage does not.
        if not _has_valid_character_name(pm, hp_addr):
            return 0.0

        score = 1.0
        penalties = 0

        # Check attribute reasonableness (soft scoring)
        for offset, name, min_val, max_val in [
            (-96, "外功", 0, 500),
            (-88, "根骨", 0, 500),
            (-80, "技巧", 0, 500),
            (44, "魅力值", 0, 500),
        ]:
            try:
                val = pm.read_int(hp_addr + offset)
                if not (min_val <= val <= max_val):
                    penalties += 1
            except Exception:
                penalties += 1

        # Check coordinates reasonableness
        for offset in [416, 420]:  # X, Y
            try:
                coord = pm.read_int(hp_addr + offset)
                if not (-1 <= coord <= 10000):
                    penalties += 1
            except Exception:
                penalties += 1

        # Detect sequential number pattern (false positive indicator)
        try:
            vals = [pm.read_int(hp_addr + off) for off in [0, 4, 8, 12, 24, 28]]
            diffs = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
            if sum(1 for d in diffs if d < 10) >= 4:
                penalties += 3
        except Exception:
            pass

        # Apply penalties
        score -= penalties * 0.1
        return max(0.0, score)

    except Exception:
        return 0.0


def verify_structure_shifted(pm, struct_base, fields):
    """Validate a 4-byte-shifted character struct layout.

    In this rare layout (observed on unequipped characters), the struct stores
    max values before current values for HP and MP:
      struct_base + 0  = max_HP   (normally current_HP)
      struct_base + 4  = current_HP (normally max_HP)
      struct_base + 8  = max_MP
      struct_base + 12 = current_MP
    All other field offsets (level, attributes, weight, coords) are unchanged.
    """
    try:
        hp_max = pm.read_int(struct_base + 0)  # swapped: max before current
        hp = pm.read_int(struct_base + 4)  # swapped: current at +4
        mp_max = pm.read_int(struct_base + 8)  # swapped
        mp = pm.read_int(struct_base + 12)  # swapped
        weight = pm.read_int(struct_base + 24)
        weight_max = pm.read_int(struct_base + 28)
        level = pm.read_int(struct_base - 36)

        # Hard constraints
        if not (1 <= hp <= hp_max <= 999999):
            return 0.0
        if not (0 <= mp <= mp_max <= 999999):
            return 0.0
        if not (0 <= weight <= weight_max <= 999999):
            return 0.0
        if not (1 <= level <= 200):
            return 0.0
        # A real character has a Big5 name here; struct-shaped garbage does not.
        if not _has_valid_character_name(pm, struct_base):
            return 0.0

        score = 1.0
        penalties = 0

        # Soft checks — same offsets from struct_base as normal layout
        for offset, _, min_val, max_val in [
            (-96, "外功", 0, 500),
            (-88, "根骨", 0, 500),
            (-80, "技巧", 0, 500),
            (44, "魅力值", 0, 500),
        ]:
            try:
                val = pm.read_int(struct_base + offset)
                if not (min_val <= val <= max_val):
                    penalties += 1
            except Exception:
                penalties += 1

        for offset in [416, 420]:
            try:
                coord = pm.read_int(struct_base + offset)
                if not (-1 <= coord <= 10000):
                    penalties += 1
            except Exception:
                penalties += 1

        score -= penalties * 0.1
        return max(0.0, score)

    except Exception:
        return 0.0


# ============================================================
# 顯示角色狀態
# ============================================================
# The current stage is a CStage reached from a fixed global; it holds the stage
# id and name inline and is reused across map changes (only its fields change).
# The 0x28 int before the name that locate_map_name keys on is the tile size
# (map px / tile count == 40), not a per-map value. Fixed for a given tthola.dat
# build, like the vtables below; 0x00808C30 holds the same pointer.
STAGE_PTR = 0x00804B64
STAGE_VTABLE = 0x005FD238  # CStage
STAGE_ID_OFFSET = 0x2AE8  # int32, == stages.id
STAGE_NAME_OFFSET = 0x2B04  # char[32] Big5, == stages.name
STAGE_NAME_MAX_BYTES = 32


def read_stage(pm):
    """(stage_id, map_name) from the CStage global, or None when it is unreachable."""
    try:
        stage = struct.unpack("<I", pm.read_bytes(STAGE_PTR, 4))[0]
        if not HEAP_MIN_PTR <= stage <= 0x7FFFFFFF:
            return None
        if struct.unpack("<I", pm.read_bytes(stage, 4))[0] != STAGE_VTABLE:
            return None
        stage_id = pm.read_int(stage + STAGE_ID_OFFSET)
        raw = pm.read_bytes(stage + STAGE_NAME_OFFSET, STAGE_NAME_MAX_BYTES)
    except Exception:
        return None
    end = raw.find(b"\x00")
    if end < 1:
        return None
    try:
        name = raw[:end].decode("big5")
    except Exception:
        return None
    return stage_id, name


def read_map_name(pm, valid_names: set[str] | None = None):
    """Current map name: direct CStage read, falling back to the heap scan.

    The fallback covers a client patch that moves STAGE_PTR / STAGE_VTABLE.
    """
    stage = read_stage(pm)
    if stage is not None and (valid_names is None or stage[1] in valid_names):
        return stage[1]
    return locate_map_name(pm, valid_names)


def locate_map_name(pm, valid_names: set[str] | None = None):
    """Scan heap for the current map name string.

    Map struct pattern (offsets from string start):
      [-4]  = 40 (0x28) — consistent across maps
      [0]   = Big5 encoded map name (2-8 Chinese characters)
      [+N]  = 0x00 terminator

    When `valid_names` is provided (set of UTF-8 stage names from tthol.sqlite),
    candidates are validated against the canonical map list — this is the only
    reliable filter after the 2026-05 game update, which broke the previous
    `0xCDCDCDCD` trailing-padding heuristic (some maps now have 2 bytes between
    the null terminator and the heap padding).

    Without `valid_names`, falls back to the legacy heuristic: trailing
    `0xCDCDCDCD` immediately after null + no `cdcd/fdfd` in preceding 8 bytes.
    The legacy path is kept for the standalone `reader.py` CLI which has no DB.

    Returns the decoded map name string, or empty string if not found.
    """
    regions = get_memory_regions(pm.process_handle)
    marker_before = b"\x28\x00\x00\x00"  # 40 as little-endian int32

    for base, size in regions:
        if base < 0x10000000 or base > 0x40000000:
            continue
        try:
            data = pm.read_bytes(base, size)
        except Exception:
            continue

        i = 0
        while i < len(data) - 24:
            idx = data.find(marker_before, i)
            if idx == -1:
                break
            i = idx + 1

            str_start = idx + 4
            if str_start + 4 >= len(data):
                continue

            hi = data[str_start]
            lo = data[str_start + 1]
            if not (0xA1 <= hi <= 0xF9 and 0x40 <= lo <= 0xFE):
                continue

            null_pos = data.find(b"\x00", str_start, str_start + 17)
            if null_pos < str_start + 2:
                continue
            name_bytes = data[str_start:null_pos]
            if len(name_bytes) % 2 != 0:
                continue

            if not all(
                0xA1 <= name_bytes[j] <= 0xF9 and 0x40 <= name_bytes[j + 1] <= 0xFE
                for j in range(0, len(name_bytes), 2)
            ):
                continue

            try:
                decoded = name_bytes.decode("big5")
            except Exception:
                continue

            if valid_names is not None:
                if decoded in valid_names:
                    return decoded
                continue

            # Legacy heuristic path (no DB whitelist available)
            after = null_pos + 1
            if after + 4 > len(data):
                continue
            if data[after : after + 4] != b"\xcd\xcd\xcd\xcd":
                continue
            if idx >= 8:
                pre = data[idx - 8 : idx]
                if b"\xcd\xcd" in pre or b"\xfd\xfd" in pre:
                    continue
            return decoded

    return ""


def read_character_name(pm, hp_addr):
    """Read null-terminated Big5 character name at HP_ADDR + NAME_OFFSET."""
    try:
        raw = pm.read_bytes(hp_addr + NAME_OFFSET, NAME_MAX_BYTES)
        end = raw.find(b"\x00")
        if end != -1:
            raw = raw[:end]
        if not raw:
            return ""
        return raw.decode("big5", errors="replace")
    except Exception:
        return ""


# In the compat (4-byte-shifted) layout the current/max HP and MP pairs are
# stored swapped relative to the normal layout (see verify_structure_shifted):
#   struct_base+0 = max_HP, +4 = current_HP, +8 = max_MP, +12 = current_MP.
# Remap exactly those four offsets so each field keeps its correct meaning;
# every other field shares the normal-layout offset.
_COMPAT_OFFSET_SWAP = {0: 4, 4: 0, 8: 12, 12: 8}


def read_all_fields(pm, hp_addr, display_fields, compat_mode=False):
    """Read all known integer fields.

    When compat_mode is True the struct uses the 4-byte-shifted layout, so the
    HP/MP current/max offsets (0/4 and 8/12) are remapped to keep 血量/最大血量/
    真氣/最大真氣 correct. All other fields share the normal-layout offsets.
    """
    result = []
    for offset, name in display_fields:
        phys = _COMPAT_OFFSET_SWAP.get(offset, offset) if compat_mode else offset
        try:
            value = pm.read_int(hp_addr + phys)
            result.append((name, value))
        except Exception:
            result.append((name, "???"))
    return result


def format_status(fields_data, char_name="", map_name=""):
    """Format character status as string."""
    lines = []
    if char_name:
        lines.append(f"  Character: {char_name}")

    hp = mp = weight = level = None
    stats = []
    combat = []

    for name, value in fields_data:
        if name == "血量":
            hp = value
        elif name == "最大血量":
            hp_max = value
        elif name == "真氣":
            mp = value
        elif name == "最大真氣":
            mp_max = value
        elif name == "負重":
            weight = value
        elif name == "最大負重":
            weight_max = value
        elif name == "等級":
            level = value
        elif name in ("外功", "根骨", "技巧"):
            stats.append((name, value))
        elif name == "魅力值":
            stats.append((name, value))
        elif name in ("X座標", "Y座標"):
            pass  # handled separately
        else:
            combat.append((name, value))

    coord_x = coord_y = None
    for name, value in fields_data:
        if name == "X座標":
            coord_x = value
        elif name == "Y座標":
            coord_y = value

    lines.append(
        f"  Lv.{level}  HP: {hp}/{hp_max}  MP: {mp}/{mp_max}  Weight: {weight}/{weight_max}"
    )
    map_str = map_name if map_name else "?"
    if coord_x is not None and coord_y is not None:
        lines.append(f"  Map: {map_str}  Pos: ({coord_x}, {coord_y})")
    else:
        lines.append(f"  Map: {map_str}")
    lines.append("  Stats: " + "  ".join(f"{n}:{v}" for n, v in stats))
    lines.append("  Combat: " + "  ".join(f"{n}:{v}" for n, v in combat))
    return "\n".join(lines)


# ============================================================
# Inventory
# ============================================================
# The HP-based char struct lives inside the engine's CCharObject (MSVC RTTI
# name), which holds the item containers as (count, pointer-array) pairs. Each
# array entry points to an item instance. No memory scan is needed once hp_addr
# is known. See docs/plans/2026-10-01-inventory-direct-read.md.
# Vtables are fixed for a given tthola.dat build; re-derive from RTTI after a
# client patch (procedure in docs/plans/2026-10-01-equipment-reading-investigation.md).
CHAR_OBJ_VTABLE = 0x005F5DC4  # CCharObject
CHAR_DATA_VTABLE = 0x005F99E0  # CCharData (holds the warehouse items)
CHAR_OBJ_HP_OFFSET = 0x2C8  # hp_addr == CCharObject + 0x2C8

MONEY_OFFSET = 0x90  # int32, relative to hp_addr
INVENTORY_COUNT_OFFSET = 0x94  # int32 count, followed by ptr -> item ptr[count]
PET_INVENTORY_COUNT_OFFSET = 0x9C

ITEM_ID_OFFSET = 0x05  # int32, unaligned, in the item instance
ITEM_QTY_OFFSET = 0x10  # int32
MAX_CONTAINER_ITEMS = 500  # sanity bound on a container count
MAX_ITEM_ID = 99999  # items.id tops out in the 5-digit range

# Warehouse: only exists while the warehouse window is open. The window manager
# (first hop of the player HP chain) holds a list of child windows; the
# warehouse window's +0x138 points 0x1A0 into a CCharData whose
# +0x1A4/+0x1A8 is the (count, array) pair. Its index in the list varies.
WINDOW_MANAGER_OFFSET = 0x128  # [PLAYER_HP_CHAIN_BASE] + 0x128 -> CWndManagerEx
WINDOW_LIST_START = 0x24  # child-window pointers start here
WINDOW_LIST_MAX = 256  # observed ~90 entries; read in chunks, stop at unmapped memory
WINDOW_LIST_CHUNK = 64
HEAP_MIN_PTR = 0x01000000  # pointer floor; windows live as low as 0x04xxxxxx, below HEAP_MIN_ADDR
WAREHOUSE_WND_DATA_OFFSET = 0x138
WAREHOUSE_DATA_DELTA = 0x1A0  # [wnd+0x138] == CCharData + 0x1A0
WAREHOUSE_COUNT_OFFSET = 0x1A4  # relative to CCharData


def load_item_db():
    """Load item name lookup from tthol.sqlite."""
    db_path = bundled("tthol.sqlite")
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM items")
    items = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return items


# ============================================================
# Active-buff list (status-group array inside the char struct)
# Defaults; overridable via knowledge.json "buff_structure".
# ============================================================
BUFF_COUNT_OFFSET = 0x288  # int32: number of active buffs
BUFF_ARRAY_OFFSET = 0x28C  # int32[]: compacting array of status `group` ids
BUFF_MAX_SLOTS = 32


def read_status_array(pm, hp_addr, count_off, array_off, max_slots):
    """Read one count+group status array relative to the HP base.

    Layout: an int32 count at count_off, then a compacting array of int32
    status-group ids at array_off. Only the first <count> slots are valid;
    trailing slots may hold stale values. Returns a list of group ids (ints),
    or [] on any read failure.
    """
    try:
        count = pm.read_int(hp_addr + count_off)
    except Exception:
        return []
    if count <= 0 or count > max_slots:
        return []
    groups = []
    for i in range(count):
        try:
            g = pm.read_int(hp_addr + array_off + i * 4)
        except Exception:
            break
        if 0 < g < 100000:  # plausible group id; skip stale/garbage
            groups.append(g)
    return groups


def read_active_buffs(pm, hp_addr, knowledge=None):
    """Read positive-buff status groups (HP+0x288). Kept for backward
    compatibility; new code should prefer read_active_statuses().
    """
    count_off, array_off, max_slots = BUFF_COUNT_OFFSET, BUFF_ARRAY_OFFSET, BUFF_MAX_SLOTS
    if knowledge:
        bs = knowledge.get("buff_structure")
        if bs:
            count_off = bs.get("count_offset", count_off)
            array_off = bs.get("array_offset", array_off)
            max_slots = bs.get("max_slots", max_slots)
    return read_status_array(pm, hp_addr, count_off, array_off, max_slots)


def read_active_statuses(pm, hp_addr, knowledge=None):
    """Read every status array declared in knowledge.json.

    A status array is any structure carrying both count_offset and array_offset
    whose value_type names status groups (buff_structure, debuff_structure, …).
    Returns a list of (group, kind) tuples, where `kind` comes from the
    structure's "kind" field (defaulting to the key minus "_structure").

    Config-driven: to cover a newly reverse-engineered status category, add its
    verified offsets + a "kind" to knowledge.json — no code change needed.
    """
    if not knowledge:
        return [
            (g, "buff")
            for g in read_status_array(
                pm, hp_addr, BUFF_COUNT_OFFSET, BUFF_ARRAY_OFFSET, BUFF_MAX_SLOTS
            )
        ]
    out = []
    for key, st in knowledge.items():
        if not isinstance(st, dict) or "count_offset" not in st or "array_offset" not in st:
            continue
        if "status" not in str(st.get("value_type", "")).lower():
            continue  # skip inventory/warehouse slot arrays
        kind = st.get("kind") or key.replace("_structure", "")
        for g in read_status_array(
            pm,
            hp_addr,
            st.get("count_offset", BUFF_COUNT_OFFSET),
            st.get("array_offset", BUFF_ARRAY_OFFSET),
            st.get("max_slots", BUFF_MAX_SLOTS),
        ):
            out.append((g, kind))
    return out


def load_status_db():
    """Map status `group` id -> representative buff name from tthol.sqlite.

    A group's statuses share a display name across levels (護體/血契/靈契...),
    so the first row per group (lowest `order`) is a good representative.
    """
    db_path = bundled("tthol.sqlite")
    if not db_path.exists():
        return {}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        cur = conn.cursor()
        cur.execute('SELECT "group", name FROM status ORDER BY "group", "order"')
        out = {}
        for group_id, name in cur.fetchall():
            if group_id not in out:
                out[group_id] = name
        return out
    except Exception:
        return {}
    finally:
        # sqlite's `with` only manages the transaction, not the handle, so close
        # explicitly here to avoid leaking the connection on the error path.
        if conn is not None:
            conn.close()


def _read_u32(pm, addr):
    return struct.unpack("<I", pm.read_bytes(addr, 4))[0]


def read_item_container(pm, count_addr):
    """Read a (count, pointer-array) item container. Returns [(item_id, qty)].

    count_addr holds the int32 count; the next dword points to an array of
    `count` item-instance pointers. Raises ValueError on an implausible count or
    array pointer. A single unreadable or implausible entry (the game may be
    reallocating the container mid-read) is skipped rather than failing the list.
    """
    count = pm.read_int(count_addr)
    if not 0 <= count <= MAX_CONTAINER_ITEMS:
        raise ValueError(f"implausible item count {count} at 0x{count_addr:08X}")
    if count == 0:
        return []
    arr = _read_u32(pm, count_addr + 4)
    if not HEAP_MIN_PTR <= arr <= 0x7FFFFFFF:
        raise ValueError(f"implausible item array pointer 0x{arr:08X}")
    ptrs = struct.unpack(f"<{count}I", pm.read_bytes(arr, 4 * count))
    items = []
    for ptr in ptrs:
        if not HEAP_MIN_PTR <= ptr <= 0x7FFFFFFF:
            continue
        try:
            item_id = struct.unpack("<i", pm.read_bytes(ptr + ITEM_ID_OFFSET, 4))[0]
            qty = pm.read_int(ptr + ITEM_QTY_OFFSET)
        except Exception:
            continue
        if 0 < item_id <= MAX_ITEM_ID:
            items.append((item_id, qty))
    return items


def is_char_object(pm, hp_addr):
    """True when hp_addr sits at the expected offset inside a CCharObject."""
    try:
        return _read_u32(pm, hp_addr - CHAR_OBJ_HP_OFFSET) == CHAR_OBJ_VTABLE
    except Exception:
        return False


def read_inventory(pm, hp_addr):
    """Bag contents as [(item_id, qty)], or None if hp_addr is not in a CCharObject."""
    if not is_char_object(pm, hp_addr):
        return None
    return read_item_container(pm, hp_addr + INVENTORY_COUNT_OFFSET)


def read_pet_inventory(pm, hp_addr):
    """Pet bag contents as [(item_id, qty)], or None if hp_addr is not in a CCharObject."""
    if not is_char_object(pm, hp_addr):
        return None
    return read_item_container(pm, hp_addr + PET_INVENTORY_COUNT_OFFSET)


def read_money(pm, hp_addr):
    """Carried money (銀兩), or None if hp_addr is not in a CCharObject."""
    if not is_char_object(pm, hp_addr):
        return None
    return pm.read_int(hp_addr + MONEY_OFFSET)


# Appearance (paper doll). Offsets are relative to hp_addr, i.e. CCharObject
# +0x298 / +0x29C / +0x4EC / +0x4F0. The engine keeps the sprite sequence it
# actually draws per layer (outfit EXTRA_* slots already applied over the
# equipped item), so the head / cap sequences are read straight from there.
# Verified live 2026-10-01 across ~15 players; see
# docs/plans/2026-10-01-equipment-reading-investigation.md.
HAIR_ITEM_OFFSET = -0x30  # int32 base-head item id (29001.. male, 29051.. female)
HAIR_COLOR_OFFSET = -0x2C  # int32 hair dye 0..10 (doll_frame_images.color)
DOLL_HEAD_SEQ_OFFSET = 0x224  # int32 doll_frame_images.sequence of the head layer
DOLL_CAP_SEQ_OFFSET = 0x228  # int32 sequence of the cap layer; DOLL_EMPTY_SEQ when bare
DOLL_EMPTY_SEQ = 96
MAX_HAIR_COLOR = 10
# Sequence = gender base + slot index * 1000 + part number.
_DOLL_GENDER_BASE = {100000: "m", 300000: "f"}
_DOLL_HEAD_SLOT = 0
_DOLL_CAP_SLOT = 1


def _doll_part(seq, slot):
    """(gender, seq) when seq is a sequence of the given doll slot, else None."""
    base = seq - seq % 100000
    gender = _DOLL_GENDER_BASE.get(base)
    if gender is None or (seq - base) // 1000 != slot or seq % 1000 == 0:
        return None
    return gender, seq


def read_appearance(pm, hp_addr):
    """Head / cap layers the client draws for this character.

    Returns {gender, hair_item, hair_color, head, cap}, where head / cap are
    doll sequences and cap is None when no hat is drawn. Returns None when
    hp_addr is not in a CCharObject or the head sequence looks wrong (object
    being rebuilt mid-read).
    """
    if not is_char_object(pm, hp_addr):
        return None
    head = _doll_part(pm.read_int(hp_addr + DOLL_HEAD_SEQ_OFFSET), _DOLL_HEAD_SLOT)
    if head is None:
        return None
    gender, head_seq = head
    cap = _doll_part(pm.read_int(hp_addr + DOLL_CAP_SEQ_OFFSET), _DOLL_CAP_SLOT)
    color = pm.read_int(hp_addr + HAIR_COLOR_OFFSET)
    return {
        "gender": gender,
        "hair_item": pm.read_int(hp_addr + HAIR_ITEM_OFFSET),
        "hair_color": color if 0 <= color <= MAX_HAIR_COLOR else 0,
        "head": head_seq,
        # A cap of the other gender cannot be drawn on this head.
        "cap": cap[1] if cap is not None and cap[0] == gender else None,
    }


# Equipment: one item-instance pointer per slot in the CCharObject (0 = empty),
# item id at ITEM_ID_OFFSET of the instance. Offsets are relative to the
# CCharObject. +0x374 (HEAD) holds the base hairstyle and is always empty, so it
# is skipped. Outfit (EXTRA_*) slots are stored elsewhere, not yet located. See
# docs/plans/2026-10-01-equipment-reading-investigation.md.
EQUIP_SLOTS = (
    (0x378, "CAP"),
    (0x37C, "BODY"),
    (0x380, "FOOT"),
    (0x384, "WING"),
    (0x388, "HORSE"),
    (0x38C, "ORNAMENT_1"),
    (0x390, "ORNAMENT_2"),
    (0x394, "ORNAMENT_3"),
    (0x398, "HAND_L"),
    (0x39C, "HAND_R"),  # also two-handed (HANDS) weapons
)
# Enhancement level: u8 in the item instance, stored as N + 10 (raw <= 10 means
# not enhanced). From the tooltip code at tthola.dat 0x47E720, which prints
# "name(+N)" with N = raw - 10. strong_equipment tops out at +20.
ENHANCE_OFFSET = 0x221
ENHANCE_BIAS = 10
MAX_ENHANCE = 20
# The instance holds the item's stats in items-table column order, int16 each,
# hp / mp followed by their flag. Values are the item's own stats with 真元
# inlays applied (enhancement bonuses are not included). Verified against the
# DB on 33 bag items and against the in-game tooltip.
ITEM_STATS_OFFSET = 0x1B0
ITEM_STAT_FIELDS = (
    # (column, offset from ITEM_STATS_OFFSET)
    ("hp", 0x00),
    ("hp_flag", 0x02),
    ("mp", 0x04),
    ("mp_flag", 0x06),
    ("str", 0x08),
    ("pow", 0x0C),
    ("vit", 0x10),
    ("dex", 0x14),
    ("agi", 0x18),
    ("wis", 0x1C),
    ("atk", 0x20),
    ("matk", 0x22),
    ("extra_def", 0x24),
    ("magic_def", 0x26),
    ("hit", 0x28),
    ("dodge", 0x2A),
    ("critical_hit", 0x30),
    ("run_speed", 0x44),
)
ITEM_STATS_SIZE = 0x46
FLAT_STAT_FLAG = 1  # hp / mp count as a flat bonus only with this flag


def read_item_stats(pm, ptr):
    """Non-zero stats of an item instance as {items column: value}."""
    raw = pm.read_bytes(ptr + ITEM_STATS_OFFSET, ITEM_STATS_SIZE)
    vals = {col: struct.unpack_from("<h", raw, off)[0] for col, off in ITEM_STAT_FIELDS}
    for col in ("hp", "mp"):
        if vals.pop(f"{col}_flag") != FLAT_STAT_FLAG:
            vals[col] = 0
    return {col: v for col, v in vals.items() if v}


def read_equipment(pm, hp_addr):
    """Equipped items as [(slot, item_id or None, plus, stats)] in EQUIP_SLOTS
    order, or None if hp_addr is not in a CCharObject. plus is the enhancement
    level (0 when none); stats is read_item_stats() of the instance. A slot whose
    pointer or id looks wrong (being swapped mid-read) reads as empty."""
    if not is_char_object(pm, hp_addr):
        return None
    obj = hp_addr - CHAR_OBJ_HP_OFFSET
    ptrs = struct.unpack(
        f"<{len(EQUIP_SLOTS)}I", pm.read_bytes(obj + EQUIP_SLOTS[0][0], 4 * len(EQUIP_SLOTS))
    )
    slots = []
    for (_off, slot), ptr in zip(EQUIP_SLOTS, ptrs):
        item_id, plus, stats = None, 0, {}
        if HEAP_MIN_PTR <= ptr <= 0x7FFFFFFF:
            try:
                value = pm.read_int(ptr + ITEM_ID_OFFSET)
                raw = pm.read_bytes(ptr + ENHANCE_OFFSET, 1)[0]
                stats = read_item_stats(pm, ptr)
            except Exception:
                value, raw, stats = 0, 0, {}
            if 0 < value <= MAX_ITEM_ID:
                item_id = value
                if ENHANCE_BIAS < raw <= ENHANCE_BIAS + MAX_ENHANCE:
                    plus = raw - ENHANCE_BIAS
            else:
                stats = {}
        slots.append((slot, item_id, plus, stats))
    return slots


def _window_list(pm):
    """Child-window pointers of the window manager; empty when unreachable (not logged in)."""
    try:
        wm = _read_u32(pm, _read_u32(pm, PLAYER_HP_CHAIN_BASE) + WINDOW_MANAGER_OFFSET)
    except Exception:
        return []
    if not HEAP_MIN_PTR <= wm <= 0x7FFFFFFF:
        return []
    windows = []
    for i in range(0, WINDOW_LIST_MAX, WINDOW_LIST_CHUNK):
        addr = wm + WINDOW_LIST_START + 4 * i
        try:
            chunk = pm.read_bytes(addr, 4 * WINDOW_LIST_CHUNK)
        except Exception:
            break
        windows.extend(struct.unpack(f"<{WINDOW_LIST_CHUNK}I", chunk))
    return windows


def locate_warehouse(pm):
    """Address of the warehouse CCharData, or None when the warehouse window is closed."""
    for wnd in _window_list(pm):
        if not HEAP_MIN_PTR <= wnd <= 0x7FFFFFFF:
            continue
        try:
            data = _read_u32(pm, wnd + WAREHOUSE_WND_DATA_OFFSET) - WAREHOUSE_DATA_DELTA
            if data >= HEAP_MIN_PTR and _read_u32(pm, data) == CHAR_DATA_VTABLE:
                return data
        except Exception:
            continue
    return None


def read_warehouse(pm):
    """Warehouse contents as [(item_id, qty)], or None when the window is closed."""
    data = locate_warehouse(pm)
    if data is None:
        return None
    return read_item_container(pm, data + WAREHOUSE_COUNT_OFFSET)


def format_inventory(items, item_db):
    """Format inventory items as string."""
    if not items:
        return "  (empty)"
    lines = []
    lines.append(f"  {'#':>3}  {'ID':>6}  {'Qty':>5}  Name")
    lines.append(f"  {'---':>3}  {'------':>6}  {'-----':>5}  ----")
    for i, (item_id, qty) in enumerate(items):
        name = item_db.get(item_id, "???")
        lines.append(f"  {i + 1:>3}  {item_id:>6}  {qty:>5}  {name}")
    lines.append(f"  Total: {len(items)} items")
    return "\n".join(lines)


# ============================================================
# Main
# ============================================================
def main():
    # Ensure UTF-8 output on Windows
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print(
            "Usage: uv run reader.py <current_hp> [--loop] [--inventory] [--filter field=value ...]"
        )
        print("  --loop              Continuous monitoring mode (updates every second)")
        print("  --inventory         Show inventory contents")
        print("  --filter field=val  Require field to equal value (repeatable)")
        print("  Example: uv run reader.py 287 --filter 等級=7 --filter 真氣=150")
        return

    hp_value = int(sys.argv[1])
    loop_mode = "--loop" in sys.argv
    show_inventory = "--inventory" in sys.argv

    # Collect all --filter arguments
    raw_filters = []
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--filter":
            if i + 1 >= len(args):
                print("[X] --filter requires an argument (e.g. --filter 等級=7)")
                raise SystemExit(1)
            raw_filters.append(args[i + 1])
            i += 2
        else:
            i += 1

    knowledge = load_knowledge()

    filters = parse_filters(raw_filters)
    offset_filters = resolve_filters(filters, knowledge) if filters else {}

    if offset_filters:
        filter_desc = ", ".join(f"{k}={v}" for k, v in filters.items())
        print(f"Filters: {filter_desc}")

    print("Connecting to Tthol...")
    try:
        pm = pymem.Pymem("tthola.dat")
    except Exception as e:
        print(f"[X] Cannot connect: {e}")
        return

    display_fields = get_display_fields(knowledge)

    print(f"Locating character struct (HP={hp_value})...")
    t0 = time.time()
    hp_addr = locate_character(pm, hp_value, knowledge, offset_filters=offset_filters)
    elapsed = time.time() - t0

    if hp_addr is None:
        print("[X] Cannot find character struct, check HP value")
        return

    print(f"[OK] Character located at 0x{hp_addr:08X} ({elapsed:.2f}s)\n")

    # Read character status
    char_name = read_character_name(pm, hp_addr)
    map_name = read_map_name(pm)
    fields_data = read_all_fields(pm, hp_addr, display_fields)
    print(format_status(fields_data, char_name, map_name))

    # Inventory
    if show_inventory:
        print(f"\n{'=' * 50}")
        print("Inventory")
        print(f"{'=' * 50}")

        item_db = load_item_db()
        if not item_db:
            print("  [!] Item DB (tthol.sqlite) not found, showing IDs only")

        items = read_inventory(pm, hp_addr)
        if items is None:
            print("[X] Located struct is not inside a CCharObject; cannot read inventory")
            return
        print(f"Money: {read_money(pm, hp_addr)}\n")
        print(format_inventory(items, item_db))

    # Loop mode
    if loop_mode:
        print("\nMonitoring... (Ctrl+C to stop)\n")
        try:
            while True:
                fields_data = read_all_fields(pm, hp_addr, display_fields)
                map_name = read_map_name(pm)
                status = format_status(fields_data, char_name, map_name)
                line_count = status.count("\n") + 1
                sys.stdout.write(f"\r\033[{line_count}A{status}\n")
                sys.stdout.flush()
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()
