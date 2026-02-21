"""
Centralised UI strings for the Tthol Reader GUI.

Usage:
    from gui.i18n import t

    label = QLabel(t("connect"))
    msg = t("items_count", n=42)   # "共 42 個道具"
"""

_STRINGS: dict[str, str] = {
    # ── Main window ──────────────────────────────────────────────────────
    "window_title": "武林小幫手",
    "placeholder_tab": "請先開啟遊戲",
    "refresh_tooltip": "掃描新遊戲視窗",
    "close_tab_tooltip": "關閉分頁",
    "no_new_windows": "未找到新遊戲視窗",
    "nav_characters": "角色",
    "nav_inventory": "道具總管",
    # ── Character panel — op bar ─────────────────────────────────────────
    "hp_input_placeholder": "當前血量",
    "connect": "連線",
    "relocate": "重新定位",
    "state_disconnected": "● 未連線",
    "enter_valid_hp": "請輸入有效的血量值",
    "focus_window": "視窗",
    "focus_window_tooltip": "將此遊戲視窗移至最上層",
    "filter_toggle_show": "+ 進階",
    "filter_toggle_hide": "▲ 進階",
    "mp_filter_label": "真氣",
    "mp_filter_placeholder": "選填",
    "enter_valid_mp": "真氣篩選值必須為正整數",
    # ── Character panel — vitals strip ───────────────────────────────────
    "vital_lv": "等級",
    "vital_hp": "血量",
    "vital_mp": "真氣",
    "vital_wt": "負重",
    "vital_pos": "座標",
    # ── Character panel — inner tabs ─────────────────────────────────────
    "tab_status": "角色狀態",
    "tab_inventory": "背包",
    "tab_warehouse": "倉庫",
    "tab_manager": "道具總覽",
    # ── Character panel — status messages ────────────────────────────────
    "no_inventory_to_save": "無背包資料可儲存",
    "no_warehouse_to_save": "無倉庫資料可儲存",
    "snapshot_saved": "快照已儲存",
    "snapshot_no_change": "無變動，略過",
    "scan_error": "[錯誤] {msg}",
    # ── Status tab ───────────────────────────────────────────────────────
    "group_basic": "基本資訊",
    "group_attributes": "屬性",
    "group_combat": "戰鬥相關",
    "field_name": "角色名稱",
    "bar_hp": "血量",
    "bar_mp": "真氣",
    "bar_weight": "負重",
    # ── Inventory tab ────────────────────────────────────────────────────
    "scan_inventory": "掃描背包",
    "save_snapshot": "儲存快照",
    "not_scanned": "尚未掃描",
    "scanning": "掃描中...",
    "ready": "就緒",
    "updated_at": "更新於 {time}",
    "items_count": "共 {n} 個道具",
    "col_seq": "#",
    "col_item_id": "道具ID",
    "col_qty": "數量",
    "col_name": "名稱",
    # ── Warehouse tab ────────────────────────────────────────────────────
    "scan_warehouse": "掃描倉庫",
    "inventory_warning": "  ⚠  請先在遊戲中開啟背包介面，再進行掃描。",
    "warehouse_warning": "  ⚠  請先在遊戲中開啟倉庫介面，再進行掃描。",
    # ── Inventory manager tab ────────────────────────────────────────────
    "search_placeholder": "搜尋道具名稱...",
    "all_characters": "所有角色",
    "all_sources": "所有來源",
    "source_inventory": "背包",
    "source_warehouse": "倉庫",
    "by_char": "依角色",
    "by_item": "依道具",
    "mgr_col_character": "角色",
    "mgr_col_item_id": "道具ID",
    "mgr_col_name": "名稱",
    "mgr_col_qty": "數量",
    "mgr_col_source": "來源",
    "mgr_col_snapshot_time": "快照時間",
    "mgr_col_total_qty": "總數量",
    "mgr_col_details": "明細",
    "char_count": "{n} 個角色",
    "summary_kinds_total": "{kinds} 種 · 共 {total} 個",
    "footer_items": "共 {n} 筆",
    # ── Snapshot management (shared by DataManagementTab and CharacterCard) ─────
    "manage": "管理",
    "account_label": "帳號歸屬",
    "no_account": "未設定",
    "create_account": "+ 建立新帳號",
    "snapshot_history": "快照歷史",
    "delete_snapshot": "刪除",
    "delete_character": "刪除此角色所有記錄",
    "confirm_delete_snapshot": "確定刪除此快照？此操作無法還原。",
    "confirm_delete_character": "確定刪除「{character}」的所有快照與帳號設定？此操作無法還原。",
    "deleted_snapshot": "已刪除快照",
    "deleted_character": "已刪除角色「{character}」的所有記錄",
    "account_assigned": "已將角色歸入帳號「{name}」",
    "enter_account_name": "輸入帳號名稱",
    "new_account_placeholder": "帳號名稱",
    # ── Data management tab ───────────────────────────────────────────────────────
    "nav_data_mgmt": "資料管理",
    "mgmt_characters_header": "角色列表",
    "mgmt_accounts_header": "帳號列表",
    "mgmt_select_character": "請選擇一個角色",
}


def t(key: str, **kwargs: object) -> str:
    """Return the localised string for *key*, with optional format substitutions."""
    s = _STRINGS.get(key, key)
    return s.format(**kwargs) if kwargs else s
