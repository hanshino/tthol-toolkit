"""
Stable pointer chain finder.

Opens multiple game processes simultaneously (by PID) and discovers pointer
chains that resolve to the correct HP address in ALL processes.  Chains that
survive the cross-process intersection are stable across game restarts.

Usage:
    uv run find_stable_chain.py <pid1>:<hp1> <pid2>:<hp2> [...]

Example:
    uv run find_stable_chain.py 12340:33698 15672:48377
"""

import sys
import struct
import time

import pymem

from reader import (
    get_memory_regions,
)


# ============================================================
# Memory scanning infrastructure
# ============================================================


def get_static_ranges(pm):
    """Return list of (base, end, module_name) for all loaded modules."""
    static_ranges = []
    for module in pm.list_modules():
        base = module.lpBaseOfDll
        size = module.SizeOfImage
        static_ranges.append((base, base + size, module.name))
    return static_ranges


def is_static(address, static_ranges):
    """Check if address falls within a loaded module (static/green address)."""
    for base, end, name in static_ranges:
        if base <= address < end:
            return True, name, address - base
    return False, None, None


def find_pointers_to(pm, target_addr, search_range, regions):
    """Find all 4-byte aligned values in memory that point to
    [target_addr - search_range, target_addr].

    Returns list of (found_addr, ptr_value, offset) tuples where
    offset = target_addr - ptr_value.
    """
    results = []
    low = target_addr - search_range
    high = target_addr

    for region_base, region_size in regions:
        try:
            buffer = pm.read_bytes(region_base, region_size)
            for i in range(0, len(buffer) - 3, 4):
                ptr_value = struct.unpack("<I", buffer[i : i + 4])[0]
                if low <= ptr_value <= high:
                    results.append((region_base + i, ptr_value, target_addr - ptr_value))
        except Exception:
            pass

    return results


# ============================================================
# Find all addresses containing a given int32 value
# ============================================================


def find_all_hp_addrs(pm, hp_value):
    """Scan all readable memory for 4-byte-aligned int32 == hp_value.
    Returns list of addresses."""
    regions = get_memory_regions(pm.process_handle)
    target_bytes = struct.pack("<i", hp_value)
    addrs = []

    for base, size in regions:
        try:
            buffer = pm.read_bytes(base, size)
            offset = 0
            while True:
                pos = buffer.find(target_bytes, offset)
                if pos == -1:
                    break
                if pos % 4 == 0:
                    addrs.append(base + pos)
                offset = pos + 1
        except Exception:
            pass

    return addrs


# ============================================================
# BFS reverse pointer scan
# ============================================================

MAX_LEVELS = 5
SEARCH_RANGE = 2048
MAX_TARGETS_PER_LEVEL = 200


def reverse_scan_multi(pm, start_addrs):
    """BFS from multiple start addresses simultaneously back toward static bases.

    Reads memory only once per level, scanning for pointers to ALL targets at once.
    This is much faster than calling reverse_scan() for each address separately.

    Returns list of chains.  Each chain is a dict:
        module: str          -- module name (e.g. "tthola.dat")
        module_offset: int   -- offset within module
        offsets: list[int]   -- per-level offsets (from outermost to innermost)
        start_addr: int      -- which starting address this chain leads to
    """
    regions = get_memory_regions(pm.process_handle)
    static_ranges = get_static_ranges(pm)

    # Each entry: (current_target_addr, offsets_so_far, original_start_addr)
    current_targets = [(addr, [], addr) for addr in start_addrs]
    found_chains = []

    for level in range(MAX_LEVELS):
        print(f"    Level {level + 1}: scanning {len(current_targets)} targets ...", flush=True)
        next_targets = []

        # Build a single merged range covering all targets
        # Group targets and scan memory once per region
        target_list = [(addr, offsets, start) for addr, offsets, start in current_targets]

        # Sort targets by address for efficient range checking
        target_list.sort(key=lambda t: t[0])
        # Global min/max for quick rejection
        global_low = target_list[0][0] - SEARCH_RANGE
        global_high = target_list[-1][0]

        for region_base, region_size in regions:
            try:
                buffer = pm.read_bytes(region_base, region_size)
            except Exception:
                continue

            for i in range(0, len(buffer) - 3, 4):
                ptr_value = struct.unpack("<I", buffer[i : i + 4])[0]
                # Quick rejection using global bounds
                if ptr_value < global_low or ptr_value > global_high:
                    continue

                found_addr = region_base + i

                # Check this pointer against all current targets
                for target_addr, offsets_so_far, start in target_list:
                    offset = target_addr - ptr_value
                    if 0 <= offset <= SEARCH_RANGE:
                        new_offsets = [offset] + offsets_so_far

                        ok, module_name, module_offset = is_static(found_addr, static_ranges)
                        if ok:
                            found_chains.append(
                                {
                                    "module": module_name,
                                    "module_offset": module_offset,
                                    "offsets": new_offsets,
                                    "start_addr": start,
                                }
                            )
                        else:
                            next_targets.append((found_addr, new_offsets, start))

        # Deduplicate next targets by address (keep first occurrence)
        seen = set()
        unique = []
        for addr, offsets, start in next_targets:
            if addr not in seen:
                seen.add(addr)
                unique.append((addr, offsets, start))
        next_targets = unique

        static_count = len(found_chains)
        print(
            f"    Level {level + 1}: {len(next_targets)} dynamic, {static_count} static chains found",
            flush=True,
        )

        if not next_targets and not found_chains:
            print("    No more pointers to trace", flush=True)
            break

        current_targets = next_targets[:MAX_TARGETS_PER_LEVEL]

    return found_chains


# ============================================================
# Chain resolution & verification
# ============================================================


def resolve_chain_addr(pm, module_name, module_offset, offsets):
    """Resolve a pointer chain and return the final address it points to, or None."""
    try:
        base_addr = None
        for module in pm.list_modules():
            if module.name == module_name:
                base_addr = module.lpBaseOfDll + module_offset
                break
        if base_addr is None:
            return None

        addr = base_addr
        for off in offsets:
            ptr = struct.unpack("<I", pm.read_bytes(addr, 4))[0]
            if ptr == 0 or ptr > 0x7FFFFFFF:
                return None
            addr = ptr + off

        return addr
    except Exception:
        return None


def chain_key(chain):
    """Produce a hashable key for intersection: (module, module_offset, tuple(offsets))."""
    return (chain["module"], chain["module_offset"], tuple(chain["offsets"]))


# ============================================================
# Main
# ============================================================


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    if len(sys.argv) < 3:
        print("Usage: uv run find_stable_chain.py <pid1>:<hp1> <pid2>:<hp2> [...]")
        print()
        print("Open 2+ game windows, note each PID and current HP value.")
        print("PID can be found in Task Manager (Details tab).")
        print()
        print("Example:")
        print("  uv run find_stable_chain.py 12340:33698 15672:48377")
        return

    # Parse arguments
    targets = []
    for arg in sys.argv[1:]:
        if ":" not in arg:
            print(f"[X] Invalid format '{arg}', expected pid:hp")
            return
        pid_str, hp_str = arg.split(":", 1)
        try:
            targets.append((int(pid_str), int(hp_str)))
        except ValueError:
            print(f"[X] Invalid format '{arg}', pid and hp must be integers")
            return

    print(f"Targets: {len(targets)} processes")
    for pid, hp in targets:
        print(f"  PID {pid} -> HP {hp}")
    print()

    # Step 1 & 2: For each process, find ALL addresses with HP value, reverse scan each
    per_process_data = []

    for i, (pid, hp) in enumerate(targets):
        print(f"[{i + 1}/{len(targets)}] Connecting to PID {pid} ...")
        try:
            pm = pymem.Pymem(pid)
        except Exception as e:
            print(f"  [X] Cannot connect to PID {pid}: {e}")
            return

        print(f"  Scanning for all addresses containing HP={hp} ...", flush=True)
        t0 = time.time()
        hp_addrs = find_all_hp_addrs(pm, hp)
        elapsed = time.time() - t0
        print(f"  [OK] Found {len(hp_addrs)} addresses ({elapsed:.1f}s)", flush=True)
        for a in hp_addrs:
            print(f"    0x{a:08X}", flush=True)

        # Run reverse BFS from ALL HP addresses simultaneously (single memory pass)
        print(
            f"  Running reverse pointer scan from {len(hp_addrs)} starting points ...", flush=True
        )
        t0 = time.time()
        all_chains = reverse_scan_multi(pm, hp_addrs)
        elapsed = time.time() - t0
        print(f"  [OK] Found {len(all_chains)} static chains total ({elapsed:.1f}s)", flush=True)

        per_process_data.append(
            {
                "pid": pid,
                "hp": hp,
                "hp_addrs": hp_addrs,
                "pm": pm,
                "chains": all_chains,
            }
        )
        print()

    # Step 3: Intersect chains across all processes
    print("=" * 60)
    print("Intersecting chains across processes ...")

    if not per_process_data:
        print("[X] No process data")
        return

    # Build set of chain keys for each process
    key_sets = []
    for proc in per_process_data:
        keys = set(chain_key(c) for c in proc["chains"])
        key_sets.append(keys)
        print(f"  PID {proc['pid']}: {len(keys)} unique chains")

    # Intersection
    common_keys = key_sets[0]
    for ks in key_sets[1:]:
        common_keys = common_keys & ks

    print(f"  Intersection: {len(common_keys)} chains survived")
    print()

    if not common_keys:
        print("[X] No stable chains found across all processes.")
        print("    Possible causes:")
        print("    - search_range too small (currently 2048)")
        print("    - max_levels too low (currently 5)")
        print("    - The game may not have a stable chain for this HP value")
        for proc in per_process_data:
            proc["pm"].close_process()
        return

    # Step 4: Verify — resolve each surviving chain, classify as current/max HP
    print("Verifying surviving chains ...")
    verified = []

    for key in sorted(common_keys):
        module, module_offset, offsets = key
        all_ok = True
        results = []

        for proc in per_process_data:
            resolved_addr = resolve_chain_addr(proc["pm"], module, module_offset, list(offsets))
            if resolved_addr is None:
                ok = False
                hp_at_addr = None
            else:
                try:
                    hp_at_addr = proc["pm"].read_int(resolved_addr)
                except Exception:
                    hp_at_addr = None
                ok = hp_at_addr == proc["hp"] or resolved_addr in proc["hp_addrs"]
            results.append((proc["pid"], proc["hp"], resolved_addr, hp_at_addr, ok))
            if not ok:
                all_ok = False

        if all_ok:
            verified.append((module, module_offset, offsets, results))

    print(f"  Verified: {len(verified)} chains resolve correctly in all processes")
    print()

    if not verified:
        print("[X] No chains passed verification.")
        for proc in per_process_data:
            proc["pm"].close_process()
        return

    # Step 5: Classify each chain — does it point to current HP or max HP?
    # OOP sub-object layout: +0x130 = max HP, +0x140 = current HP (0x10 apart)
    # Check adjacent memory to determine which field the chain resolves to.
    print("Classifying chains (current HP vs max HP) ...")
    classified = []

    for module, module_offset, offsets, results in verified:
        field_votes = {"current_hp": 0, "max_hp": 0, "unknown": 0}

        for proc in per_process_data:
            resolved_addr = resolve_chain_addr(proc["pm"], module, module_offset, list(offsets))
            if resolved_addr is None:
                field_votes["unknown"] += 1
                continue
            try:
                val_here = proc["pm"].read_int(resolved_addr)
                val_plus16 = proc["pm"].read_int(resolved_addr + 0x10)
                val_minus16 = proc["pm"].read_int(resolved_addr - 0x10)
            except Exception:
                field_votes["unknown"] += 1
                continue

            # If [addr+0x10] is a valid HP-like value and >= val_here → we're at max HP
            # (max HP is before current HP in the struct: +0x130=max, +0x140=current)
            # If [addr-0x10] is a valid HP-like value and >= val_here → we're at current HP
            if 1 <= val_plus16 <= 999999 and val_plus16 <= val_here:
                # addr has larger or equal value, addr+0x10 has smaller or equal
                # → addr = max HP, addr+0x10 = current HP
                field_votes["max_hp"] += 1
            elif 1 <= val_minus16 <= 999999 and val_minus16 >= val_here:
                # addr-0x10 has larger or equal value
                # → addr = current HP, addr-0x10 = max HP
                field_votes["current_hp"] += 1
            else:
                # When at full HP, both are equal — check if both neighbors look like HP
                if val_plus16 == val_here and 1 <= val_here <= 999999:
                    field_votes["max_hp"] += 1  # assume max (more common in scan)
                elif val_minus16 == val_here and 1 <= val_here <= 999999:
                    field_votes["current_hp"] += 1
                else:
                    field_votes["unknown"] += 1

        # Determine classification
        if field_votes["max_hp"] > field_votes["current_hp"]:
            field_type = "max_hp"
        elif field_votes["current_hp"] > field_votes["max_hp"]:
            field_type = "current_hp"
        else:
            field_type = "unknown"

        classified.append((module, module_offset, offsets, results, field_type))

    # Cleanup process handles
    for proc in per_process_data:
        proc["pm"].close_process()

    # Deduplicate: group by (module, module_offset, field_type) and keep shortest chain
    seen_bases = {}
    for module, module_offset, offsets, results, field_type in classified:
        base_key = (module, module_offset)
        if base_key not in seen_bases or len(offsets) < len(seen_bases[base_key][2]):
            seen_bases[base_key] = (module, module_offset, offsets, results, field_type)

    unique_chains = sorted(seen_bases.values(), key=lambda x: len(x[2]))

    # Output results
    print("=" * 60)
    print(f"STABLE CHAINS ({len(unique_chains)} unique bases)")
    print("=" * 60)

    for i, (module, module_offset, offsets, results, field_type) in enumerate(unique_chains):
        offsets_list = list(offsets)

        # Build CE-style notation for found chain
        inner = f"{module}+0x{module_offset:X}"
        for off in offsets_list:
            if off == 0:
                inner = f"[{inner}]"
            else:
                inner = f"[{inner}]+0x{off:X}"

        field_label = {"max_hp": "max HP", "current_hp": "current HP", "unknown": "HP (unknown)"}[
            field_type
        ]
        print(f"\n[{i + 1}] {inner}  ({field_label})")

        for pid, expected_hp, resolved_addr, hp_at_addr, ok in results:
            mark = "OK" if ok else "FAIL"
            addr_str = f"0x{resolved_addr:08X}" if resolved_addr else "None"
            print(f"    PID {pid}: addr={addr_str}, value={hp_at_addr} [{mark}]")

        # Suggest constants — output both current HP and max HP chains
        suggested_base = 0x00400000 + module_offset
        if field_type == "max_hp":
            current_offsets = offsets_list[:-1] + [offsets_list[-1] + 0x10]
            max_offsets = offsets_list
        elif field_type == "current_hp":
            current_offsets = offsets_list
            max_offsets = offsets_list[:-1] + [offsets_list[-1] - 0x10]
        else:
            current_offsets = offsets_list
            max_offsets = offsets_list

        print("\n    Suggested constants for reader.py:")
        print(f"      PLAYER_HP_CHAIN_BASE = 0x{suggested_base:08X}")
        print(f"      # Current HP offsets: {current_offsets}")
        print(f"      # Max HP offsets:     {max_offsets}")
        print(f"      PLAYER_HP_CHAIN_OFFSETS = {current_offsets}  # current HP")

    print()


if __name__ == "__main__":
    main()
