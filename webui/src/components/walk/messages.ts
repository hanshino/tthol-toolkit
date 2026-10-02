// Planner reasons (services/walk_path.py) in the UI's words.
export const WALK_REASONS: Record<string, string> = {
  'no walk mask for this map': '此地圖沒有可行走資料',
  'target is not walkable': '那裡走不到（不是可行走的地面）',
  'player is not on a walkable tile': '角色不在可行走的格子上',
  'target is not reachable on foot': '走路到不了（要經過傳點）',
  'no clickable step from here': '路線中途找不到能點的位置',
  'too many steps': '路線太長',
};

// Runner messages (services/walker.py) in the UI's words.
export const WALK_FAILURES: Record<string, string> = {
  'character position is not readable': '讀不到角色位置',
  'map changed': '地圖換了，已停止',
  'stuck: not getting closer': '卡住了，走不過去',
  'game window not found': '找不到遊戲視窗',
  'clicked an NPC or player instead of the ground': '點到 NPC 或玩家了，已停止（可能開了對話框）',
  'the game went somewhere else (camera offset?)': '遊戲走去別的地方了，已停止',
  'click was not taken (UI in the way?)': '點擊沒生效，可能被介面擋住',
  'teleported to another map': '被傳送到別張地圖，已停止',
  'teleported': '被傳送了，已停止',
  'the player clicked somewhere else': '偵測到手動操作，已停止',
  'too many steps': '路線太長，已停止',
  stopped: '已停止',
  'lost contact': '和程式失去連線，請確認遊戲中的角色',
  'could not end the drag: follow-the-cursor mode may still be on': '拖曳模式可能還開著：請在遊戲裡點一下角色取消',
};
