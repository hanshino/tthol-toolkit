"""
檢查角色數據結構
"""
import pymem
import struct
import sys

TARGET_ADDR = 0x22DEBA60

def dump_structure(pm, addr, before=128, after=256):
    """dump 角色數據結構"""
    start = addr - before
    size = before + after
    try:
        data = pm.read_bytes(start, size)
    except Exception:
        print(f"[X] 無法讀取 0x{start:08X}")
        return

    print(f"=== 角色數據結構 (base: 0x{addr:08X}) ===\n")
    print(f"{'offset':>8} | {'hex':>10} | {'int32':>12} | note")
    print("-" * 60)

    known = {
        0: "血量",
        4: "最大血量",
        8: "真氣",
        12: "最大真氣",
        24: "負重",
        28: "最大負重",
        32: "???",
        44: "魅力值",
    }

    for i in range(0, size, 4):
        offset = i - before
        raw = data[i:i+4]
        if len(raw) < 4:
            break

        val_i32 = struct.unpack('<i', raw)[0]
        hex_str = raw.hex()

        marker = known.get(offset, "")
        if marker == "" and 1 <= val_i32 <= 999999:
            marker = "?"

        print(f"  {offset:>+5}  | {hex_str:>10} | {val_i32:>12} | {marker}")

def main():
    addr = TARGET_ADDR
    if len(sys.argv) > 1:
        addr = int(sys.argv[1], 16)

    print("正在連接到 Tthol...")
    pm = pymem.Pymem("tthola.dat")
    print("[OK] 連接成功！\n")

    dump_structure(pm, addr, before=128, after=256)

if __name__ == "__main__":
    main()
