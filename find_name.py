"""
Character name finder for Tthol.
Searches memory for the character name string (Big5 / GBK / UTF-8),
and also scans for string-like data near the known HP address.

Usage:
    uv run find_name.py <current_hp> <character_name>

Example:
    uv run find_name.py 46277 YourCharName
"""
import sys
import struct
import time
import pymem
import ctypes
import ctypes.wintypes

# Re-use infrastructure from reader.py
sys.path.insert(0, '.')
from reader import get_memory_regions, locate_character, load_knowledge


ENCODINGS = ['big5', 'gbk', 'utf-8']

# ============================================================
# Search memory for a byte pattern
# ============================================================
def search_bytes(pm, regions, pattern):
    """Return list of addresses where pattern occurs."""
    hits = []
    for base, size in regions:
        try:
            buffer = pm.read_bytes(base, size)
        except Exception:
            continue
        offset = 0
        while True:
            pos = buffer.find(pattern, offset)
            if pos == -1:
                break
            hits.append(base + pos)
            offset = pos + 1
    return hits


# ============================================================
# Read null-terminated string at address (try multiple encodings)
# ============================================================
def read_cstring(pm, addr, max_len=64):
    """Read null-terminated bytes and decode with fallback."""
    try:
        raw = pm.read_bytes(addr, max_len)
    except Exception:
        return None, None
    end = raw.find(b'\x00')
    if end == 0:
        return None, None
    raw = raw[:end] if end != -1 else raw
    for enc in ['big5', 'gbk', 'utf-8', 'latin-1']:
        try:
            return raw.decode(enc), enc
        except Exception:
            pass
    return raw.hex(), 'hex'


# ============================================================
# Scan struct neighbourhood for pointers that might lead to strings
# ============================================================
def scan_struct_pointers(pm, hp_addr, scan_range=(-256, 512)):
    """Check every int32 in range from hp_addr; if it looks like a valid
    heap pointer, try to read a string at that address."""
    print(f"\n[Pointer scan] Scanning offsets {scan_range[0]}..{scan_range[1]} from HP addr for pointers to strings")
    start = scan_range[0]
    end = scan_range[1]
    found = []
    for off in range(start, end, 4):
        try:
            raw = pm.read_bytes(hp_addr + off, 4)
            ptr = struct.unpack('<I', raw)[0]
        except Exception:
            continue
        # Valid 32-bit heap pointer range
        if not (0x01000000 <= ptr <= 0x7FFFFFFF):
            continue
        # Try to read string at pointer
        text, enc = read_cstring(pm, ptr)
        if text and len(text) >= 2 and len(text) <= 32:
            # Filter: at least one printable non-ASCII char or purely ASCII name
            printable = all(32 <= b < 127 or b >= 0x81 for b in text.encode('latin-1', errors='replace'))
            if printable:
                print(f"  HP+{off:+d}  ptr=0x{ptr:08X}  -> '{text}'  [{enc}]")
                found.append((off, ptr, text, enc))
    if not found:
        print("  (no string pointers found in this range)")
    return found


# ============================================================
# Dump raw bytes near HP addr (look for inline strings)
# ============================================================
def dump_strings_near_hp(pm, hp_addr, before=512, after=1024):
    """Scan raw bytes before/after HP addr, report any decodable Chinese strings."""
    print(f"\n[Inline string scan] Scanning {before} bytes before and {after} bytes after HP addr")
    try:
        raw = pm.read_bytes(hp_addr - before, before + after)
    except Exception:
        print("  Cannot read memory region")
        return

    # Walk through and find runs of Big5/GBK bytes
    # Big5 lead byte: 0x81-0xFE, trail byte: 0x40-0xFE (except 0x7F)
    i = 0
    while i < len(raw) - 1:
        b = raw[i]
        if 0x81 <= b <= 0xFE:
            # Possible Big5/GBK lead byte - try to grab a run
            j = i
            while j < len(raw) - 1:
                lb = raw[j]
                if 0x81 <= lb <= 0xFE and 0x40 <= raw[j + 1] <= 0xFE and raw[j + 1] != 0x7F:
                    j += 2
                elif 0x20 <= lb < 0x7F:
                    j += 1
                else:
                    break
            run = raw[i:j]
            if len(run) >= 4:
                for enc in ['big5', 'gbk']:
                    try:
                        text = run.decode(enc)
                        offset_from_hp = (hp_addr - before + i) - hp_addr
                        print(f"  HP+{offset_from_hp:+d}  '{text}'  [{enc}]")
                        break
                    except Exception:
                        pass
            i = j
        else:
            i += 1


# ============================================================
# Main
# ============================================================
def main():
    sys.stdout.reconfigure(encoding='utf-8')

    if len(sys.argv) < 3:
        print("Usage: uv run find_name.py <current_hp> <character_name>")
        print("  character_name  Your in-game character name (Chinese or ASCII)")
        return

    hp_value = int(sys.argv[1])
    char_name = sys.argv[2]

    print("Connecting to Tthol...")
    try:
        pm = pymem.Pymem("tthola.dat")
    except Exception as e:
        print(f"[X] Cannot connect: {e}")
        return

    knowledge = load_knowledge()

    print(f"Locating character struct (HP={hp_value})...")
    t0 = time.time()
    hp_addr = locate_character(pm, hp_value, knowledge)
    elapsed = time.time() - t0
    if hp_addr is None:
        print("[X] Cannot find character struct, check HP value")
        return
    print(f"[OK] HP address: 0x{hp_addr:08X}  ({elapsed:.2f}s)")

    regions = get_memory_regions(pm.process_handle)

    # --- 1. Direct string search ---
    print(f"\n[Direct search] Searching for name: '{char_name}'")
    all_hits = []
    for enc in ENCODINGS:
        try:
            encoded = char_name.encode(enc)
        except Exception:
            print(f"  Cannot encode '{char_name}' as {enc}, skipping")
            continue
        hits = search_bytes(pm, regions, encoded)
        if hits:
            for addr in hits:
                rel = addr - hp_addr
                print(f"  [{enc}]  0x{addr:08X}  (HP+{rel:+d})")
            all_hits.extend(hits)
        else:
            print(f"  [{enc}]  not found")

    # --- 2. Pointer scan near struct ---
    scan_struct_pointers(pm, hp_addr, scan_range=(-512, 1024))

    # --- 3. Inline string scan near struct ---
    dump_strings_near_hp(pm, hp_addr, before=512, after=1024)

    print("\nDone.")


if __name__ == '__main__':
    main()
