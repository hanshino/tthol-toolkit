import type { ErrorInfo } from '../api/types';

export function friendlyError(e: ErrorInfo): string {
  switch (e.code) {
    case 'E_WH_NOT_FOUND':
      return '庫房尚未讀取 — 請先在遊戲中打開倉庫視窗';
    case 'E_INV_NOT_FOUND':
      return '找不到背包資料 — 可換張地圖後按「↻ 重偵」';
    case 'E_LOCATE_EXHAUSTED':
      // Not necessarily a login problem: when a game update invalidates the
      // pointer chain, 重偵 re-runs the same dead chain and can never succeed.
      // Manual HP is the fallback that still works, so lead with it.
      return '找不到角色 — 若已登入仍失敗，請於下方輸入目前血量定位';
    case 'E_PROC_GONE':
      return '無法連上遊戲程式 — 遊戲可能已關閉';
    case 'E_CHAIN_READ':
      return '尚未登入角色';
    default:
      return e.message;
  }
}
