"""
Warehouse reader.
Reads the warehouse straight from the open warehouse window (no memory scan);
see reader.locate_warehouse. Requires the warehouse UI to be open in game.

Usage:
    uv run warehouse_scan.py
"""

import sys
import time

import pymem

from reader import (
    WAREHOUSE_COUNT_OFFSET,
    format_inventory,
    load_item_db,
    locate_warehouse,
    read_item_container,
)

sys.stdout.reconfigure(encoding="utf-8")


def main():
    print("Connecting to Tthol...")
    try:
        pm = pymem.Pymem("tthola.dat")
    except Exception as e:
        print(f"[X] Cannot connect: {e}")
        return

    item_db = load_item_db()
    t0 = time.perf_counter()
    data = locate_warehouse(pm)
    if data is None:
        print("[X] Warehouse not found -- open the warehouse UI in game first")
        return
    items = read_item_container(pm, data + WAREHOUSE_COUNT_OFFSET)
    elapsed = time.perf_counter() - t0

    print(f"[OK] Warehouse at 0x{data:08X} ({elapsed * 1000:.1f} ms)\n")
    print(format_inventory(items, item_db))


if __name__ == "__main__":
    main()
