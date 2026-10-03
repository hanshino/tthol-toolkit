# 配裝模擬器匯出（TTHOL1）：tthol-memory 實作計畫

> 狀態：**已實作**（`feat/stat-sim-export`，2026-10-03）。第 1–6 項完成，第 7 項的資料部分驗過了：天外天 Lv186「阿克婭」從 app API 和 CLI 匯出的內容一致，丟進 genbu `assembleImport` + `computePanel` 後，面板 19 項有 18 項完全一致，負重上限差 +157（在已知誤差 +0..+215 內），轉生點數 140，沒有診斷訊息。還沒驗證：「開啟 ↗」在 pywebview 是否交給系統瀏覽器，以及 compat 佈局的角色。
>
> 跟原計畫不同的地方：
> - 第 4 項沒有在 worker 加快取，改成 API 請求時用 `ReaderWorker.read_locked()` 在目前的鎖定位址即時讀取；讀的過程中鎖定換了就作廢（回 409）。
> - 第 6 項的「開啟」按鈕用 `window.open`，pywebview 的 `OPEN_EXTERNAL_LINKS_IN_BROWSER`（預設 True）會交給系統瀏覽器，不另做 `webbrowser.open` API。
> 契約（以它為準）：genbu `feat/stat-sim-import` 分支的 `docs/plans/2026-10-03-stat-sim-import-format.md`。
> genbu 匯入端：`src/lib/stat-sim-import-codec.ts`（validator）、`src/lib/types/stat-sim-import.ts`、`src/lib/stat-sim-import.ts`。
> CLI：`scripts/stat_sim_export.py`（不開 app，讀所有開著的 client，輸出 JSON 和 TTHOL1 字串；`uv run python scripts/stat_sim_export.py <輸出目錄>`）。原本的原型已由它取代。

## 目標

角色頁按「複製到配裝模擬器」，產出 `TTHOL1.<base64url(deflate-raw(JSON))>`，貼到 genbu `/tools/stat-sim` 就建好角色。tthol-memory 只匯出讀到的原始值，所有換算（轉生推算、裝備拆成插槽 / 隨機素質、被動篩選）都在 genbu。

原型已經從兩個實機 client 產出合法字串，genbu 的匯入測試 80 個全過，兩隻角色匯入後六圍和大部分面板值都跟遊戲完全一致。

## 匯出內容與來源

| 欄位 | 來源（相對 HP 位址，另註明者除外） |
|---|---|
| `name` / `sect` / `level` | −228 Big5 / −196 / −36 |
| `bare`（不含裝六圍） | −264..−244，順序外功 / 內力 / 根骨 / 身法 / 技巧 / 玄學 |
| `remainingPoints` | −32 |
| `equipment.<slot>` | `read_equipment`；slot 對應 CAP→cap、BODY→body、FOOT→foot、HAND_R→right、HAND_L→left、WING→wing、HORSE→horse、ORNAMENT_1..3→ornament1..3 |
| `.inlays` | 實例 +0x226/+0x22A/+0x22E/+0x232 的 u16 compounds.id，**長度固定 4、保留 0**（現在的 `read_item_inlays` 會丟掉 0，要另做） |
| `.stats` | 實例 +0x1B0 的數值（含插槽與隨機素質，不含強化）；key 換成 genbu：extra_def→def、magic_def→mdef、critical_hit→critical；**不放 damage_***（genbu 遇到未知 stats key 會整筆拒絕） |
| `.damage` / `.zhenjie` / `.refineLeft` | 實例 +0x1B0+0x38..0x3E / +0x218 u32 / +0x220 u8（選填） |
| `skills` | `read_skills` 全部 `{id: level}` |
| `panel` | −96..−76、最大體力 / 真氣（**compat 佈局要套 `_COMPAT_OFFSET_SWAP`**）、+72 +80 +84 +88 +92 +96 +100 +104、+64 攻速、+60 移動、+28 負重上限 |
| `appearance` | `read_appearance`；讀不到時性別改用髮型 id（−0x30：29001.. 男、29051.. 女） |
| `statuses` | `read_active_statuses`，分 buffs（+0x288）/ debuffs（+0x4C4），存 status.group |

## 工作項目

1. **knowledge.json 補欄位**（tthol_data 2026-10-03 審核過）：
   - −264..−244 不含裝六圍、−32 剩餘點數；−48 / −44 原標「未知」，改成髮型 id / 髮色。
   - +60 移動速度 = 5 + 坐騎 / 飾品 run_speed，拿掉「unverified」。
   - 裝備欄 CCharObject +0x378..+0x39C；道具實例：+0x1B0 數值區（含 +0x38..+0x3E 傷害、+0x44 run_speed）、+0x218 真解、+0x21C（對比用，用途不明）、+0x220 剩餘煉化次數、+0x221 強化（N+10）、+0x226.. 插槽（從最後一格往前填）。
   - HP 鏈 `[[[0x804B90]+0x128]+0x68]`（+0x140 目前 / +0x130 上限）；locate_method 裡「no stable static chain」已過時。
2. **reader.py**：`read_bare_attrs`、`read_remaining_points`、保留位置的插槽讀取、`read_item_extras`（真解、剩餘煉化次數）。附測試。
3. **`services/stat_sim_export.py`**：純函式 `build_payload(...)` + `encode()`（`zlib.compressobj(9, zlib.DEFLATED, -15)`、base64url 不補 `=`、前綴 `TTHOL1.`）。測試含 round-trip 與 fixture。
4. **worker**：匯出需要的資料由 worker 讀好快取（equipment / skills / appearance 已有 callback，要補 bare / remaining / panel / statuses / 道具額外欄位），或在 worker 的 pm 下即時讀。先讀 `services/worker.py` 的 `_auto_read_items` 再決定。
5. **API**：`GET /api/characters/{pid}/stat-sim-export` → `{ code, url }`，url = `https://genbu.hanshino.dev/tools/stat-sim#import=<code>`。測試時可設環境變數 `TTHOL_GENBU_URL`（例如 `http://localhost:3000`）改連別的 genbu。角色還沒定位、或裝備 / 技能還沒讀到時回錯誤，不匯出殘缺資料。
6. **webui**：角色頁加「複製到配裝模擬器」（剪貼簿）和開啟連結按鈕（pywebview 要透過 API 呼叫 `webbrowser.open`）。資料還沒好時停用。UI 改動前先查 ui-ux-pro-max skill。
7. **驗證**：從實際 app 產字串，丟進 genbu 解碼 / 匯入，跟遊戲面板對照。

## 已知事實 / 陷阱

- 屬性丹不會出現在 buff 陣列；`bare` 不含丹藥（實測）。偵測要靠「面板六圍 − bare − 裝備 − 被動 ≠ 0」，genbu 那邊做。
- 道具實例數值不含強化，genbu 自己加。
- 隨機素質裝備（meta_group）同名會有多個 id，例如天御蒼龍甲 50444 的 DB 固定值是空的；一律匯出實例上的 id。
- 晨曦破空（移花宮 Lv192）的體力固定比模擬多 1000，是伺服器端給這隻角色的加成，客戶端查不到來源，**不是匯出的 bug**。
- 內勁在內力很低時可能差 1（內力 9 那筆，騎 22391 地獄燭龍之劍），仍待查，公式在 tthol_data `scripts/stat_formula_investigation.md`。
- 本 repo 的 `tthol.sqlite` 比 genbu / tthol_data 舊（沒有 `item_rand_counts`），這也是拆解放在 genbu 的原因之一。

## 參考

- 公式與實測：tthol_data `scripts/stat_formula_investigation.md`、`scripts/zhenjie_investigation.md`（真解格式 §2、mod_count §2.5）；genbu `docs/stat-simulator-formulas.md`。
- 實機樣本：genbu `docs/plans/stat-sim-import-samples/`（`*-ext` 含選填欄位；genbu 測試依賴的是非 `-ext` 那兩份，不要覆蓋）。
