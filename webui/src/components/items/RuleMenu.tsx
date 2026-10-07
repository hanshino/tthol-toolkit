import { useEffect, useRef, useState } from 'react';
import type { ItemAction } from '../../api/types';
import { ACTION_LABEL, LATER_ACTIONS, type ItemRulesState } from './useItemRules';

/** The 處置 actions every one of `ids` allows (an item not described yet allows none). */
export function commonActions(state: ItemRulesState, ids: number[]): ItemAction[] {
  if (ids.length === 0) return [];
  const lists = ids.map(id => state.facts.get(id)?.actions ?? []);
  return lists[0].filter(a => lists.every(l => l.includes(a)));
}

/** The action all of `ids` share now, or null when they differ. */
function sharedAction(state: ItemRulesState, ids: number[]): ItemAction | null {
  const acts = ids.map(id => state.rules[id]?.action ?? 'keep');
  return acts.every(a => a === acts[0]) ? acts[0] : null;
}

function sharedKeep(state: ItemRulesState, ids: number[]): number {
  const keeps = ids.map(id => state.rules[id]?.keep ?? 0);
  return keeps.every(k => k === keeps[0]) ? keeps[0] : 0;
}

/** Pick one 處置 for `ids`: segmented buttons, plus a keep count for sell / store. */
export function RulePicker({ state, ids, onDone }: {
  state: ItemRulesState; ids: number[]; onDone?: () => void;
}) {
  const actions = commonActions(state, ids);
  const current = sharedAction(state, ids);
  const [keep, setKeep] = useState(() => sharedKeep(state, ids));
  if (actions.length === 0) return <span className="inv-rule-note">讀取中…</span>;
  const apply = (action: ItemAction) => {
    state.setMany(ids, action, LATER_ACTIONS.includes(action) ? keep : undefined);
    onDone?.();
  };
  const later = current !== null && LATER_ACTIONS.includes(current);
  return (
    <div className="inv-pick">
      <div className="inv-pick-seg" role="radiogroup" aria-label="處置">
        {actions.map(a => (
          <button key={a} type="button" role="radio" aria-checked={current === a}
            className={current === a ? 'is-active' : ''} onClick={() => apply(a)}>
            {ACTION_LABEL[a]}
          </button>
        ))}
      </div>
      {(later || actions.some(a => LATER_ACTIONS.includes(a))) && (
        <label className="inv-rule-keep">
          賣／存時保留
          <input type="number" min={0} value={keep} aria-label="保留數量"
            onChange={e => {
              const k = Math.max(0, Math.floor(Number(e.target.value)) || 0);
              setKeep(k);
              if (later) state.setMany(ids, current, k);
            }} />
          個
        </label>
      )}
    </div>
  );
}

/** Right-click menu at the pointer: the 處置 of one item, or of the picked items. */
export function RuleMenu({ state, ids, title, at, onClose, onDetail }: {
  state: ItemRulesState;
  ids: number[];
  title: string;
  at: { x: number; y: number };
  onClose: () => void;
  onDetail?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState(at);

  useEffect(() => {
    const el = ref.current;
    if (el) {
      // Keep the menu inside the window.
      const r = el.getBoundingClientRect();
      setPos({
        x: Math.max(4, Math.min(at.x, window.innerWidth - r.width - 4)),
        y: Math.max(4, Math.min(at.y, window.innerHeight - r.height - 4)),
      });
      el.querySelector<HTMLButtonElement>('button[aria-checked="true"], button')?.focus();
    }
    const outside = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    const away = () => onClose();
    window.addEventListener('mousedown', outside);
    window.addEventListener('keydown', key);
    window.addEventListener('scroll', away, true);
    window.addEventListener('resize', away);
    return () => {
      window.removeEventListener('mousedown', outside);
      window.removeEventListener('keydown', key);
      window.removeEventListener('scroll', away, true);
      window.removeEventListener('resize', away);
    };
  }, [at, onClose]);

  return (
    <div ref={ref} className="inv-menu" role="dialog" aria-label={`${title}的處置`}
      style={{ left: pos.x, top: pos.y }} onContextMenu={e => e.preventDefault()}>
      <div className="inv-menu-title">{title}</div>
      <RulePicker state={state} ids={ids} onDone={onClose} />
      {onDetail && (
        <button type="button" className="inv-menu-link" onClick={() => { onDetail(); onClose(); }}>查看詳細</button>
      )}
    </div>
  );
}
