import { useCallback, useEffect, useRef, useState } from 'react';
import { get, put } from '../../api/client';
import type { ItemAction, ItemRuleCandidate, ItemRules, ItemRulesView } from '../../api/types';
import { reportClientError } from '../../diag/report';

// 道具處置 for one character (services/item_rules.py), saved per character name.
const POLL_MS = 3000;
const SAVE_DELAY_MS = 400;

export const ACTION_LABEL: Record<ItemAction, string> = {
  keep: '不動',
  use_periodic: '定期使用',
  use_on_status: '中了狀態就用',
  sell: '賣掉',
  store: '存倉',
};
export const ACTION_SHORT: Partial<Record<ItemAction, string>> = {
  use_periodic: '定', use_on_status: '解', sell: '賣', store: '存',
};
/** Carried out by 補給 (sell at the shop, store at the 錢莊伙計), leaving `keep`. */
export const LATER_ACTIONS: ItemAction[] = ['sell', 'store'];

export type ItemRulesState = {
  character: string | null;
  rules: ItemRules['items'];
  facts: Map<number, ItemRuleCandidate>;
  error: string | null;
  setAction: (itemId: number, action: ItemAction) => void;
  setKeep: (itemId: number, keep: number) => void;
  /** One 處置 for many items (multi-select, right-click); `keep` for sell / store. */
  setMany: (itemIds: number[], action: ItemAction, keep?: number) => void;
  reload: () => void;
};

/** `extra`: item ids to describe besides the bag and pet bag (warehouse items). */
export function useItemRules(pid: number, extra: number[]): ItemRulesState {
  const [character, setCharacter] = useState<string | null>(null);
  const [rules, setRules] = useState<ItemRules['items']>({});
  const [facts, setFacts] = useState<Map<number, ItemRuleCandidate>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const dirty = useRef(false);
  const timer = useRef<number | null>(null);
  const extraKey = [...new Set(extra)].sort((a, b) => a - b).join(',');

  const load = useCallback(async () => {
    try {
      const v = await get<ItemRulesView>(`/api/characters/${pid}/item-rules?extra=${extraKey}`);
      setCharacter(v.character);
      setFacts(new Map(v.candidates.map(c => [c.item_id, c])));
      if (!dirty.current) setRules(v.rules.items);
    } catch (e) {
      reportClientError(e, { component: 'useItemRules.load', silent: true });
    }
  }, [pid, extraKey]);

  useEffect(() => {
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const save = (next: ItemRules['items']) => {
    setRules(next);
    dirty.current = true;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/item-rules`, { items: next });
        setError(null);
      } catch (e) {
        setError('處置設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'useItemRules.save', silent: true });
      } finally {
        dirty.current = false;
      }
    }, SAVE_DELAY_MS);
  };

  return {
    character,
    rules,
    facts,
    error,
    setAction: (itemId, action) => {
      const next = { ...rules };
      if (action === 'keep') delete next[itemId];
      else next[itemId] = { action, keep: next[itemId]?.keep ?? 0 };
      save(next);
    },
    setKeep: (itemId, keep) => {
      const cur = rules[itemId];
      if (cur) save({ ...rules, [itemId]: { ...cur, keep: Math.max(0, Math.floor(keep) || 0) } });
    },
    setMany: (itemIds, action, keep) => {
      const next = { ...rules };
      for (const id of itemIds) {
        if (action === 'keep') delete next[id];
        else next[id] = { action, keep: keep ?? next[id]?.keep ?? 0 };
      }
      save(next);
    },
    reload: () => { dirty.current = false; load(); },
  };
}
