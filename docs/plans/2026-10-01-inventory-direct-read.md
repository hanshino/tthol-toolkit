# 背包直接讀取：調查紀錄

2026-10-01。原本的背包定位（`reader.locate_inventory`）是全記憶體比對 0x8E0 槽的樣式，要好幾秒，**這版客戶端已經找不到了**（回傳 `None`）。追裝備時發現角色物件裡直接存著背包陣列的指標，不用掃描。遊戲執行中實測過，背包清單和銀兩都跟遊戲畫面一致。

已實作：`reader.read_inventory` / `read_pet_inventory` / `read_money` / `locate_warehouse` / `read_warehouse`，worker 與 `warehouse_scan.py` 都改用這些函式，舊的 0x8E0 槽掃描已移除。銀兩和寵物背包也跟背包一起每次輪詢讀取，顯示在行囊頁（`CharacterDetail.money` / `pet_inventory`）。

## 結論

- HP 結構就是 `CCharObject`（客戶端有 MSVC RTTI 可查類別名稱）：**HP 位址 = 物件 + `0x2C8`**。名稱 HP−228 = 物件 +0x1E4，跟裝備調查（`2026-10-01-equipment-reading-investigation.md`）是同一個物件。
- 物件裡有三組「數量 + 指標陣列」，指標指向道具實例。worker 已經有 `hp_addr`，所以讀背包不需要任何掃描，實測 0.3 ms。
- 順帶找到**銀兩**。

## 欄位（相對 HP 位址）

| HP 相對 | 物件相對 | 型別 | 內容 | 依據 |
|---|---|---|---|---|
| `+0x8C` | `+0x354` | int32 | 2000，未確認 | |
| `+0x90` | `+0x358` | int32 | **銀兩** | 實測 277956，與遊戲一致 |
| `+0x94` | `+0x35C` | int32 | 背包道具數 | 實測 38 |
| `+0x98` | `+0x360` | ptr | → 背包的 `ptr[數量]` | 清單與遊戲一致 |
| `+0x9C` | `+0x364` | int32 | 寵物背包道具數 | 實測 8 |
| `+0xA0` | `+0x368` | ptr | → 寵物背包的 `ptr[數量]` | 使用者確認是寵物背包 |
| `+0xA4` | `+0x36C` | int32 | 第三個容器道具數 | 實測 0，**未確認是什麼**；開倉庫時仍是 0，不是倉庫 |
| `+0xA8` | `+0x370` | ptr | → 第三個容器 | |
| `+0xAC` | `+0x374` | ptr | 裝備欄開始（見裝備調查） | |

## 道具實例

裝備欄和背包指向的是同一種實例：

| 位移 | 型別 | 內容 |
|---|---|---|
| `+0x00` | u8 | 意義未確認（背包第一格 1，其他 0） |
| `+0x01` | u8 | 所屬容器？背包 0、寵物背包 1 |
| `+0x05` | int32（未對齊） | 道具 ID，對照 `tthol.sqlite` 的 `items.id` |
| `+0x10` | int32 | 數量 |
| `+0x14` | Big5，null 結尾 | 道具名稱，跟 `items.name` 一致 |

## 讀法

```
count = read_i32(hp_addr + 0x94)
arr   = read_u32(hp_addr + 0x98)
for p in read_u32_array(arr, count):
    item_id = read_i32(p + 0x05)
    qty     = read_i32(p + 0x10)
```

## 怎麼找到的

1. 道具實例前面有一段 `0xCD`（MSVC debug heap 的未初始化填充）。用「`0xCD` ×16 之後，`+5` 的 ID 在 `items` 表裡，`+0x14` 的 Big5 名稱等於 `items.name`」掃出 623 個實例（約 3 秒）。
2. 反查指向這些實例的指標：只有 70 個，最大一群是 45 個連續指標，在自己角色物件後面不遠。
3. 那段前後有 `FDFDFDFD` 和 `_CrtMemBlockHeader`，是另外配置的區塊。反查指向區塊的指標，找到物件 `+0x360` / `+0x368`，前一格就是數量。

## 倉庫

倉庫不在角色物件裡。開倉庫 UI 時，它是一個 `CCharData`（vtable `0x5F99E0`），由倉庫視窗指著：

```
WM  = [[0x00804B90] + 0x128]      ; CWndManagerEx，跟 HP 鏈前兩跳相同
WM 從 +0x24 起是一排子視窗指標（約 100 個）
對每個視窗 wnd：data = [wnd + 0x138] - 0x1A0，若 [data] == CCharData vtable → 倉庫
count = [data + 0x1A4]、arr = [data + 0x1A8]
```

- 倉庫視窗在列表裡的位置**會變**（第一個角色 `WM+0x12C`、第二個角色 `WM+0x16C`，而第二個角色的 `WM+0x12C` 是別的 `CWndMultiStatic`），所以要逐一檢查，不能寫死。整個檢查約 1 ms。
- 關閉倉庫後，原本的視窗槽會清成 0、視窗被釋放（`0xDD`），`CCharData` 仍留著舊內容但沒有找到穩定路徑。所以維持「倉庫 UI 開著才讀得到」。
- 驗證：兩個角色（67 格、53 格）清單與遊戲一致；重開倉庫後視窗重建、`CCharData` 位址不變。

## 待解問題

1. 第三個容器（`+0xA4` / `+0xA8`）是什麼：開倉庫時仍是 0，不是倉庫。
2. `+0x8C` 是什麼（兩個角色分別是 2000、1800，負重上限？）。
3. 實例 `+0x00` / `+0x01` 的意義。
4. compat 佈局的角色（HP/MP current↔max 對調）`hp_addr` 是 struct_base，位移是否一樣，要找一個 compat 角色確認。`read_inventory` 會先驗證 `hp_addr - 0x2C8` 是 `CCharObject`，對不上就回報 `E_INV_NOT_FOUND`，不會讀到錯的指標。
