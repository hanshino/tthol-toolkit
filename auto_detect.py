"""
自動偵測角色結構 - 不需要已知血量值
利用角色結構的特徵模式掃描記憶體
"""
import pymem
import ctypes
import ctypes.wintypes
import struct
import json
import time
import sys

from reader import MEMORY_BASIC_INFORMATION, MEM_COMMIT, READABLE_PAGES, get_memory_regions, load_knowledge, get_display_fields, read_all_fields, format_status


def scan_for_character(pm):
    """用結構特徵掃描，不需要已知值"""
    regions = get_memory_regions(pm.process_handle)
    knowledge = load_knowledge()
    fields = knowledge['character_structure']['fields']

    total_size = sum(s for _, s in regions)
    scanned = 0
    candidates = []

    for base, size in regions:
        try:
            buffer = pm.read_bytes(base, size)
            # 每 4 bytes 對齊掃描
            for pos in range(0, len(buffer) - 100, 4):
                # 快速篩選: offset 0 = HP, offset 4 = MaxHP
                hp = struct.unpack_from('<i', buffer, pos)[0]
                max_hp = struct.unpack_from('<i', buffer, pos + 4)[0]

                # HP 和 MaxHP 必須在合理範圍，且 HP <= MaxHP
                if not (100 <= hp <= 999999 and 100 <= max_hp <= 999999):
                    continue
                if hp > max_hp:
                    continue

                # MaxHP 不應比 HP 大太多 (排除假陽性如 HP=332, MaxHP=907848)
                if max_hp > hp * 3:
                    continue

                # 排除連續遞增數列 (假資料特徵)
                if max_hp - hp <= 1 and hp > 100:
                    # 再檢查 MP 是不是也在遞增
                    mp_raw = struct.unpack_from('<i', buffer, pos + 8)[0]
                    if abs(mp_raw - max_hp) <= 2:
                        continue

                # MP 檢查: offset 8, 12
                mp = struct.unpack_from('<i', buffer, pos + 8)[0]
                max_mp = struct.unpack_from('<i', buffer, pos + 12)[0]
                if not (1 <= mp <= 999999 and 1 <= max_mp <= 999999):
                    continue
                if mp > max_mp:
                    continue
                if max_mp > mp * 3:
                    continue

                # HP 應該遠大於等級 (等級192的角色HP有46277)
                # 等級檢查: offset -36
                if pos < 96:  # 確保負偏移不越界
                    continue

                level = struct.unpack_from('<i', buffer, pos - 36)[0]
                if not (1 <= level <= 999):
                    continue

                # HP 應至少是等級的 10 倍 (合理假設)
                if hp < level * 10:
                    continue

                # 屬性檢查: 外功(-96), 根骨(-88), 技巧(-80)
                wai_gong = struct.unpack_from('<i', buffer, pos - 96)[0]
                gen_gu = struct.unpack_from('<i', buffer, pos - 88)[0]
                ji_qiao = struct.unpack_from('<i', buffer, pos - 80)[0]

                if not (1 <= wai_gong <= 9999 and 1 <= gen_gu <= 9999 and 1 <= ji_qiao <= 9999):
                    continue

                # 負重: offset 24, 28
                weight = struct.unpack_from('<i', buffer, pos + 24)[0]
                max_weight = struct.unpack_from('<i', buffer, pos + 28)[0]
                if not (0 <= weight <= 999999 and 1 <= max_weight <= 999999):
                    continue
                if weight > max_weight:
                    continue

                # 戰鬥屬性不應遠大於HP (排除假資料如防禦=31006)
                # 戰鬥屬性: 物攻(72), 防禦(84), 命中(92), 閃躲(96)
                atk = struct.unpack_from('<i', buffer, pos + 72)[0]
                defense = struct.unpack_from('<i', buffer, pos + 84)[0]
                hit = struct.unpack_from('<i', buffer, pos + 92)[0]
                dodge = struct.unpack_from('<i', buffer, pos + 96)[0]

                if not (1 <= atk <= 99999 and 1 <= defense <= 99999):
                    continue
                if not (1 <= hit <= 99999 and 1 <= dodge <= 99999):
                    continue
                # 戰鬥屬性都不應超過 HP (正常遊戲裡攻防 < HP)
                if atk > hp or defense > hp or hit > hp or dodge > hp:
                    continue

                # HP 和 MP 不應幾乎相等 (真正角色 HP 通常遠大於 MP)
                if mp > 0 and hp > 0:
                    ratio = hp / mp
                    if 0.8 < ratio < 1.2:
                        continue

                # 物攻(72) 和 物攻基礎(76) 應相近
                atk_base = struct.unpack_from('<i', buffer, pos + 76)[0]
                if atk_base <= 0 or abs(atk - atk_base) > atk * 0.5:
                    continue

                # 最後檢查: 這些值不能全都很接近 (排除遞增序列)
                vals = [hp, mp, level, wai_gong, gen_gu, atk, defense]
                vals_sorted = sorted(vals)
                max_gap = max(vals_sorted[i+1] - vals_sorted[i] for i in range(len(vals_sorted)-1))
                if max_gap < 10:
                    continue

                addr = base + pos
                candidates.append({
                    'addr': addr,
                    'hp': hp, 'max_hp': max_hp,
                    'mp': mp, 'max_mp': max_mp,
                    'level': level,
                    'stats': (wai_gong, gen_gu, ji_qiao),
                    'atk': atk, 'defense': defense,
                })

            scanned += size
            pct = scanned / total_size * 100
            print(f"  掃描進度: {pct:.0f}%  候選: {len(candidates)}", end='\r')
        except Exception:
            scanned += size

    print(f"\n  掃描完成，找到 {len(candidates)} 個候選")
    return candidates, knowledge


def main():
    print("連接到 Tthol...")
    try:
        pm = pymem.Pymem("tthola.dat")
    except Exception as e:
        print(f"[X] 無法連接: {e}")
        return

    print("自動掃描角色結構 (不需要已知血量)...\n")

    t0 = time.time()
    candidates, knowledge = scan_for_character(pm)
    elapsed = time.time() - t0

    if not candidates:
        print(f"\n[X] 找不到角色結構 ({elapsed:.2f}s)")
        return

    # 去重: 相同 HP/MaxHP/Level 的只保留第一個
    seen = set()
    unique = []
    for c in candidates:
        key = (c['hp'], c['max_hp'], c['level'])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    print(f"\n找到 {len(unique)} 個不同角色 ({elapsed:.2f}s):\n")

    display_fields = get_display_fields(knowledge)

    for i, c in enumerate(unique):
        print(f"--- 角色 {i+1} ---")
        print(f"  地址: 0x{c['addr']:08X}")
        fields_data = read_all_fields(pm, c['addr'], display_fields)
        print(format_status(fields_data))
        print()

    if '--loop' in sys.argv and unique:
        addr = unique[0]['addr']
        print(f"持續監控角色 1 (0x{addr:08X})... Ctrl+C 停止\n")
        try:
            while True:
                fields_data = read_all_fields(pm, addr, display_fields)
                status = format_status(fields_data)
                sys.stdout.write(f"\r\033[3A{status}\n")
                sys.stdout.flush()
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n停止監控")


if __name__ == "__main__":
    main()
