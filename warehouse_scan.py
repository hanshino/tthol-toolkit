"""
Warehouse reader.
Auto-locates warehouse by finding all item-slot arrays in memory,
then excluding the known inventory array.
Requires warehouse UI to be open (data is freed when closed).

Usage:
    uv run warehouse_scan.py <current_hp>
"""
import pymem
import struct
import sys
import time

from reader import (
    get_memory_regions,
    locate_inventory,
    locate_character,
    find_inventory_start,
    read_inventory,
    load_item_db,
    load_knowledge,
    INVENTORY_SLOT_SIZE,
)

sys.stdout.reconfigure(encoding='utf-8')

SLOT_SIZE = INVENTORY_SLOT_SIZE  # 2272 = 0x8E0
MAX_WAREHOUSE_SLOTS = 80


def locate_all_slot_arrays(pm):
    """Find ALL item-slot arrays in memory using the same pattern as inventory.
    Returns list of addresses (first matched slot in each array)."""
    regions = get_memory_regions(pm.process_handle)
    zero8 = b'\x00' * 8
    zero24 = b'\x00' * 24
    hits = []
    found_ranges = []  # track (start, end) to avoid duplicate hits in same array

    for base, size in regions:
        if size < SLOT_SIZE * 2:
            continue
        try:
            buffer = pm.read_bytes(base, size)
        except Exception:
            continue

        for pos in range(8, len(buffer) - SLOT_SIZE - 32, 4):
            addr = base + pos
            # Skip if inside an already-found array
            skip = False
            for rs, re in found_ranges:
                if rs <= addr < re:
                    skip = True
                    break
            if skip:
                continue

            if buffer[pos - 8:pos] != zero8:
                continue
            item_id = struct.unpack('<i', buffer[pos:pos + 4])[0]
            if not (1000 <= item_id <= 65535):
                continue
            ptr = struct.unpack('<I', buffer[pos + 4:pos + 8])[0]
            if not (0x01000000 <= ptr <= 0x7FFFFFFF):
                continue
            if buffer[pos + 8:pos + 32] != zero24:
                continue
            # Verify next slot
            ns = pos + SLOT_SIZE
            if ns + 32 > len(buffer):
                continue
            next_id = struct.unpack('<i', buffer[ns:ns + 4])[0]
            if not (1000 <= next_id <= 65535):
                continue
            next_ptr = struct.unpack('<I', buffer[ns + 4:ns + 8])[0]
            if not (0x01000000 <= next_ptr <= 0x7FFFFFFF):
                continue
            if buffer[ns + 8:ns + 32] != zero24:
                continue

            hits.append(addr)
            # Mark a generous range to skip
            found_ranges.append((addr, addr + SLOT_SIZE * MAX_WAREHOUSE_SLOTS))

    return hits


def walk_back_to_start(pm, addr):
    """Walk backward to find the first slot of an array."""
    while True:
        prev = addr - SLOT_SIZE
        try:
            item_id = pm.read_int(prev)
            if item_id == 0:
                addr = prev
                continue
            if 1000 <= item_id <= 65535:
                ptr = struct.unpack('<I', pm.read_bytes(prev + 4, 4))[0]
                if ptr == 0 or (0x01000000 <= ptr <= 0x7FFFFFFF):
                    addr = prev
                    continue
        except Exception:
            pass
        break
    return addr


def read_slot_array(pm, base_addr, max_slots=MAX_WAREHOUSE_SLOTS):
    """Read all slots from an array. Returns list of (item_id, qty, addr)."""
    items = []
    empty_streak = 0
    for i in range(max_slots):
        addr = base_addr + i * SLOT_SIZE
        try:
            item_id = pm.read_int(addr)
        except Exception:
            break

        if item_id == 0:
            empty_streak += 1
            if empty_streak > 10:
                break
            continue

        if item_id < 0 or item_id > 65535:
            break

        empty_streak = 0
        try:
            ptr = struct.unpack('<I', pm.read_bytes(addr + 4, 4))[0]
            qty = pm.read_int(ptr) if (0x01000000 <= ptr <= 0x7FFFFFFF) else -1
        except Exception:
            qty = -1

        items.append((item_id, qty, addr))

    return items


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run warehouse_scan.py <current_hp>")
        print("  Requires warehouse UI to be open in game.")
        return

    hp_value = int(sys.argv[1])

    print("Connecting to Tthol...")
    try:
        pm = pymem.Pymem("tthola.dat")
    except Exception as e:
        print(f"[X] Cannot connect: {e}")
        return

    item_db = load_item_db()
    knowledge = load_knowledge()

    # Locate character (validates HP)
    print(f"Locating character (HP={hp_value})...")
    hp_addr = locate_character(pm, hp_value, knowledge)
    if hp_addr is None:
        print("[X] Cannot find character struct, check HP value")
        return
    print(f"  Character at 0x{hp_addr:08X}")

    # Locate inventory for exclusion
    print("Locating inventory...")
    inv_match = locate_inventory(pm)
    if inv_match:
        inv_start = find_inventory_start(pm, inv_match)
        inv_end = inv_start + SLOT_SIZE * 60
        print(f"  Inventory: 0x{inv_start:08X} - 0x{inv_end:08X}")
    else:
        inv_start = inv_end = 0
        print("  [!] Inventory not found")

    # Find all slot arrays
    print("Scanning for all item-slot arrays...")
    t0 = time.time()
    all_arrays = locate_all_slot_arrays(pm)
    elapsed = time.time() - t0
    print(f"  Found {len(all_arrays)} slot arrays ({elapsed:.2f}s)")

    # Exclude inventory
    warehouse_arrays = []
    for addr in all_arrays:
        arr_start = walk_back_to_start(pm, addr)
        if inv_start and inv_start <= arr_start < inv_end:
            continue
        if inv_start and inv_start <= addr < inv_end:
            continue
        warehouse_arrays.append(arr_start)

    # Deduplicate
    warehouse_arrays = sorted(set(warehouse_arrays))
    print(f"  Non-inventory arrays: {len(warehouse_arrays)}")

    if not warehouse_arrays:
        print("\n[X] No warehouse found. Is the warehouse UI open?")
        return

    for wi, arr_start in enumerate(warehouse_arrays):
        items = read_slot_array(pm, arr_start)
        if len(items) < 2:
            continue  # skip tiny arrays (probably not warehouse)

        print(f"\n{'='*60}")
        print(f"Array #{wi+1} at 0x{arr_start:08X}  ({len(items)} items)")
        print(f"{'='*60}")
        print(f"  {'#':>3}  {'ID':>6}  {'Qty':>5}  Name")
        print(f"  {'---':>3}  {'------':>6}  {'-----':>5}  ----")
        for i, (item_id, qty, addr) in enumerate(items):
            name = item_db.get(item_id, "???")
            print(f"  {i+1:>3}  {item_id:>6}  {qty:>5}  {name}")
        print(f"  Total: {len(items)} items")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
