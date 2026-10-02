<p align="center">
  <img src="icon.png" width="128" alt="御心鑒">
</p>

<h1 align="center">御心鑒</h1>

<p align="center">Tthol 的即時資訊面板 · 只讀記憶體，不改遊戲</p>

御心鑒讀取遊戲程式在記憶體裡的資料，把角色狀態、背包、技能、所在地圖整理成一個面板。它**只讀記憶體、不寫入**，不改任何數值，也不送封包。「輔助」和「帶我去」這兩個功能會對遊戲視窗模擬滑鼠點擊，其他功能都只是看。

同時開好幾個遊戲視窗也沒問題，每個角色各自一個分頁。

## 功能

### 全角色

| 分頁 | 內容 |
|---|---|
| **總覽** | 所有開著的角色一覽：血量、真氣、等級、身上狀態 |
| **帳房** | 跨角色、跨倉庫搜尋道具，找「那把劍放在誰身上」 |
| **市價** | 擺攤行情：誰在哪裡賣什麼、賣多少，點一下就走過去 |
| **留影** | 背包快照，事後比對前後差異 |

### 單一角色

| 分頁 | 內容 |
|---|---|
| **行囊** | 背包道具與銀兩 |
| **根脈** | 六維屬性、戰鬥數值、身上裝備 |
| **武學** | 已學技能與等級 |
| **行止** | 所在地圖、小地圖（出口 / NPC / 怪物），點地圖直接移動 |
| **市集** | 逛市集時自動記錄看過的攤位 |
| **輔助** | 英雄培養自動點擊 |

<!-- TODO(screenshots): add one screenshot per section above -->

## 安裝

1. 到 [Releases](../../releases) 下載最新版 zip
2. 解壓縮到任意資料夾
3. 雙擊 `tthol-reader.exe`，跳出 UAC 時按「是」

要更新時，下載新版 zip 解壓縮覆蓋即可。

## 常見問題

**為什麼要系統管理員權限？**
讀取其他程式的記憶體是 Windows 保護的操作，一般權限讀不到。御心鑒只讀，不會寫入。

**防毒軟體跳警告？**
讀其他程式記憶體的行為，和某些惡意程式看起來很像，所以偶爾會被誤判。可以把資料夾加入白名單；原始碼都在這個 repo，可以自行檢查。

**一直顯示「連線中」？**
角色要先登入進到遊戲畫面才定位得到。停在登入畫面太久會停止嘗試，進遊戲後點開該角色，按上方的「↻ 重偵」即可。

**遊戲更新後讀不到了？**
遊戲改版可能讓定位方式失效，請等新版本釋出。

---

<details>
<summary>開發者</summary>

```bash
uv sync                              # Python dependencies
uv run scripts/db_release.py pull    # fetch tthol.sqlite (pinned by db.lock.json)
uv run app.py                        # run the app

# frontend dev mode
uv run app.py --dev
cd webui && npm install && npm run dev

uv run pytest
```

架構說明見 [CLAUDE.md](CLAUDE.md)。

</details>
