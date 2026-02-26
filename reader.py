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
import os
import sys
import time


# ============================================================
# Stable pointer chain (updated after game patches via find_stable_chain.py)
# CE notation: [[[[0x007F6810]+0x128]+0x68]+0x140]
# ============================================================
PLAYER_HP_CHAIN_BASE = 0x007F6810
PLAYER_HP_CHAIN_OFFSETS = [0x128, 0x68, 0x140]


def read_hp_from_player_chain(pm):
    """Read current HP value via the stable cross-restart pointer chain.

    Returns HP as int, or None if the chain is broken.
    """
    try:
        addr = PLAYER_HP_CHAIN_BASE
        for off in PLAYER_HP_CHAIN_OFFSETS:
            ptr = struct.unpack("<I", pm.read_bytes(addr, 4))[0]
            if ptr == 0 or ptr > 0x7FFFFFFF:
                return None
            addr = ptr + off
        hp = pm.read_int(addr)
        if hp <= 0 or hp > 500000:
            return None
        return hp
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
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge.json")
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
                if pos % 4 == 0:
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
                    if pos % 4 == 0 and pos >= 4:
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
            except:
                penalties += 1

        # Check coordinates reasonableness
        for offset in [416, 420]:  # X, Y
            try:
                coord = pm.read_int(hp_addr + offset)
                if not (-1 <= coord <= 10000):
                    penalties += 1
            except:
                penalties += 1

        # Detect sequential number pattern (false positive indicator)
        try:
            vals = [pm.read_int(hp_addr + off) for off in [0, 4, 8, 12, 24, 28]]
            diffs = [abs(vals[i + 1] - vals[i]) for i in range(len(vals) - 1)]
            if sum(1 for d in diffs if d < 10) >= 4:
                penalties += 3
        except:
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
            except:
                penalties += 1

        for offset in [416, 420]:
            try:
                coord = pm.read_int(struct_base + offset)
                if not (-1 <= coord <= 10000):
                    penalties += 1
            except:
                penalties += 1

        score -= penalties * 0.1
        return max(0.0, score)

    except Exception:
        return 0.0


# ============================================================
# 顯示角色狀態
# ============================================================
NAME_OFFSET = -228
NAME_MAX_BYTES = 32


def locate_map_name(pm):
    """Scan heap for the current map name string.

    Map struct pattern (offsets from string start):
      [-4]  = 40 (0x28) — consistent across maps
      [0]   = Big5 encoded map name (2-8 Chinese characters)
      [+N]  = 0x00 terminator
      [+N+1..+N+4] = 0xCDCDCDCD (uninitialized heap padding)

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
            # Look for the [-4]=40 marker
            idx = data.find(marker_before, i)
            if idx == -1:
                break
            i = idx + 1

            str_start = idx + 4
            if str_start + 4 >= len(data):
                continue

            # First two bytes must be a valid Big5 lead/trail pair
            h = data[str_start]
            l = data[str_start + 1]
            if not (0xA1 <= h <= 0xF9 and 0x40 <= l <= 0xFE):
                continue

            # Read the full Big5 string (up to 16 bytes = 8 chars)
            null_pos = data.find(b"\x00", str_start, str_start + 17)
            if null_pos < str_start + 2:
                continue
            name_bytes = data[str_start:null_pos]
            if len(name_bytes) % 2 != 0:
                continue

            # All bytes must be valid Big5 pairs
            if not all(
                0xA1 <= name_bytes[j] <= 0xF9 and 0x40 <= name_bytes[j + 1] <= 0xFE
                for j in range(0, len(name_bytes), 2)
            ):
                continue

            # Must be followed by 0xCDCDCDCD (uninitialized heap)
            after = null_pos + 1
            if after + 4 > len(data):
                continue
            if data[after : after + 4] != b"\xcd\xcd\xcd\xcd":
                continue

            # Preceding 8 bytes must not be cdcd/fdfd (not freed/uninitialized)
            if idx >= 8:
                pre = data[idx - 8 : idx]
                if b"\xcd\xcd" in pre or b"\xfd\xfd" in pre:
                    continue

            try:
                return name_bytes.decode("big5")
            except Exception:
                continue

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


def read_all_fields(pm, hp_addr, display_fields):
    """Read all known integer fields."""
    result = []
    for offset, name in display_fields:
        try:
            value = pm.read_int(hp_addr + offset)
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
INVENTORY_SLOT_SIZE = 2272  # 0x8E0 bytes per slot
MAX_INVENTORY_SLOTS = 60


def load_item_db():
    """Load item name lookup from tthol.sqlite."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tthol.sqlite")
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM items")
    items = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return items


def locate_inventory(pm):
    """Locate inventory array by pattern-matching the slot structure.
    Pattern: [8 zero bytes][item_id 1000-65535][valid pointer][24 zero bytes]
    Verified by checking the next slot at +2272 bytes has the same pattern."""
    regions = get_memory_regions(pm.process_handle)
    zero8 = b"\x00" * 8
    zero24 = b"\x00" * 24

    for base, size in regions:
        if size < INVENTORY_SLOT_SIZE * 2:
            continue
        try:
            buffer = pm.read_bytes(base, size)
        except Exception:
            continue

        # Scan for the pattern at 4-byte alignment
        for pos in range(8, len(buffer) - INVENTORY_SLOT_SIZE - 32, 4):
            # Quick check: preceded by 8 zero bytes
            if buffer[pos - 8 : pos] != zero8:
                continue
            # Read item_id candidate
            item_id = struct.unpack("<i", buffer[pos : pos + 4])[0]
            if not (1000 <= item_id <= 65535):
                continue
            # Check valid pointer at +4
            ptr = struct.unpack("<I", buffer[pos + 4 : pos + 8])[0]
            if not (0x01000000 <= ptr <= 0x7FFFFFFF):
                continue
            # Check 24 zero bytes after pointer
            if buffer[pos + 8 : pos + 32] != zero24:
                continue
            # Verify next slot at +SLOT_SIZE
            ns = pos + INVENTORY_SLOT_SIZE
            if ns + 32 > len(buffer):
                continue
            next_id = struct.unpack("<i", buffer[ns : ns + 4])[0]
            if not (1000 <= next_id <= 65535):
                continue
            next_ptr = struct.unpack("<I", buffer[ns + 4 : ns + 8])[0]
            if not (0x01000000 <= next_ptr <= 0x7FFFFFFF):
                continue
            if buffer[ns + 8 : ns + 32] != zero24:
                continue
            return base + pos

    return None


def find_inventory_start(pm, first_match_addr):
    """Walk backwards from a matched slot to find the first inventory slot."""
    addr = first_match_addr
    while True:
        prev = addr - INVENTORY_SLOT_SIZE
        try:
            item_id = pm.read_int(prev)
            if 1000 <= item_id <= 65535:
                # Verify pattern: preceded by zeros, has valid pointer
                ptr = struct.unpack("<I", pm.read_bytes(prev + 4, 4))[0]
                if 0x01000000 <= ptr <= 0x7FFFFFFF:
                    addr = prev
                    continue
        except Exception:
            pass
        break
    return addr


def read_inventory(pm, inv_base):
    """Read all inventory slots. Returns list of (item_id, quantity)."""
    items = []
    empty_streak = 0
    for i in range(MAX_INVENTORY_SLOTS):
        addr = inv_base + i * INVENTORY_SLOT_SIZE
        try:
            item_id = pm.read_int(addr)
        except Exception:
            break

        if item_id == 0:
            empty_streak += 1
            if empty_streak > 3:
                break
            continue

        if item_id < 0 or item_id > 65535:
            break

        empty_streak = 0
        # Follow pointer to read quantity
        try:
            ptr = struct.unpack("<I", pm.read_bytes(addr + 4, 4))[0]
            qty = pm.read_int(ptr)
        except Exception:
            qty = -1

        items.append((item_id, qty))
    return items


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
    map_name = locate_map_name(pm)
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

        print("Locating inventory...")
        t0 = time.time()
        inv_base = locate_inventory(pm)
        if inv_base is None:
            print("[X] Cannot locate inventory array")
            return

        inv_start = find_inventory_start(pm, inv_base)
        elapsed = time.time() - t0
        print(f"[OK] Inventory at 0x{inv_start:08X} ({elapsed:.2f}s)\n")

        items = read_inventory(pm, inv_start)
        print(format_inventory(items, item_db))

    # Loop mode
    if loop_mode:
        print("\nMonitoring... (Ctrl+C to stop)\n")
        try:
            while True:
                fields_data = read_all_fields(pm, hp_addr, display_fields)
                map_name = locate_map_name(pm)
                status = format_status(fields_data, char_name, map_name)
                line_count = status.count("\n") + 1
                sys.stdout.write(f"\r\033[{line_count}A{status}\n")
                sys.stdout.flush()
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopped")


if __name__ == "__main__":
    main()
