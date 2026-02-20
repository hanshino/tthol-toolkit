"""
Verify character name at HP-228 offset.
Reads the raw bytes at hp_addr - 228 and decodes as Big5 string.
"""
import sys
import struct
import time
import pymem

sys.path.insert(0, '.')
from reader import locate_character, load_knowledge


NAME_OFFSET = -228
NAME_MAX_LEN = 32  # max bytes to read (16 Chinese chars)


def read_name(pm, hp_addr):
    """Read null-terminated Big5 string at HP_ADDR + NAME_OFFSET."""
    addr = hp_addr + NAME_OFFSET
    try:
        raw = pm.read_bytes(addr, NAME_MAX_LEN)
    except Exception as e:
        return None, None, str(e)

    # Find null terminator
    end = raw.find(b'\x00')
    if end != -1:
        raw = raw[:end]

    print(f"  Raw bytes at HP{NAME_OFFSET:+d} (0x{addr:08X}): {raw.hex()}")

    for enc in ['big5', 'gbk', 'utf-8']:
        try:
            text = raw.decode(enc)
            return text, enc, None
        except Exception:
            pass

    return raw.hex(), 'hex', None


def main():
    sys.stdout.reconfigure(encoding='utf-8')

    if len(sys.argv) < 2:
        print("Usage: uv run verify_name.py <current_hp>")
        return

    hp_value = int(sys.argv[1])

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
        print("[X] Cannot find character struct")
        return

    print(f"[OK] HP address: 0x{hp_addr:08X}  ({elapsed:.2f}s)")

    name, enc, err = read_name(pm, hp_addr)
    if err:
        print(f"[X] Read error: {err}")
    elif name:
        print(f"  Name: '{name}'  [{enc}]")
        print(f"  Offset: HP{NAME_OFFSET:+d}")
    else:
        print("  (empty or unreadable)")

    # Also dump the 32 bytes around NAME_OFFSET for inspection
    print(f"\n  Context dump (HP-256 to HP-200):")
    for off in range(-256, -196, 4):
        try:
            raw4 = pm.read_bytes(hp_addr + off, 4)
            val = struct.unpack('<i', raw4)[0]
            uval = struct.unpack('<I', raw4)[0]
            try:
                text = raw4.rstrip(b'\x00').decode('big5')
            except Exception:
                text = ''
            print(f"    HP{off:+4d}  0x{uval:08X}  ({val:10d})  {text!r}")
        except Exception:
            pass


if __name__ == '__main__':
    main()
