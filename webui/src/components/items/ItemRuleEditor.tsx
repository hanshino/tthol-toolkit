import { useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import type { CopySettingsResult, ItemAction, SettingsCharacter } from '../../api/types';
import { reportClientError } from '../../diag/report';
import { ACTION_LABEL, LATER_ACTIONS, type ItemRulesState } from './useItemRules';

const NOTE: Record<ItemAction, string> = {
  keep: '自動功能不會碰這個道具。',
  use_periodic: '身上沒有它的效果就自動使用（守護開著、有 hook、不在城裡時）。',
  use_on_status: '中了它能解的狀態就自動使用（守護開著時）。',
  sell: '補給時在商人那裡賣掉，留下指定數量。',
  store: '補給時存進錢莊伙計的倉庫，留下指定數量。',
};

/** The 處置 section of the item detail panel. */
export function ItemRuleEditor({ itemId, state }: { itemId: number; state: ItemRulesState }) {
  const fact = state.facts.get(itemId);
  const rule = state.rules[itemId];
  const action: ItemAction = rule?.action ?? 'keep';
  if (!fact) {
    return (
      <div className="inv-d-sec">
        <span className="inv-d-label">處置</span>
        <span className="inv-rule-note">讀取中…</span>
      </div>
    );
  }
  const left = fact.expires_at != null ? Math.max(0, Math.round(fact.expires_at - Date.now() / 1000)) : null;
  const id = `inv-rule-${itemId}`;
  return (
    <div className="inv-d-sec">
      <label className="inv-d-label" htmlFor={id}>處置</label>
      <div className="inv-rule-row">
        <select id={id} value={action} onChange={e => state.setAction(itemId, e.target.value as ItemAction)}>
          {fact.actions.map(a => <option key={a} value={a}>{ACTION_LABEL[a]}</option>)}
        </select>
        {LATER_ACTIONS.includes(action) && (
          <label className="inv-rule-keep">
            保留
            <input type="number" min={0} value={rule?.keep ?? 0}
              onChange={e => state.setKeep(itemId, Number(e.target.value))} />
            個
          </label>
        )}
      </div>
      <span className="inv-rule-note">
        {NOTE[action]}
        {fact.effect && ` ${fact.effect}。`}
        {action === 'use_periodic' && (fact.active
          ? <b className="inv-rule-on"> 生效中{left != null ? ` ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : ''}</b>
          : ' 目前未生效。')}
      </span>
      {fact.actions.length === 1 && <span className="inv-rule-note">這個道具不能使用、販賣或存倉，只能不動。</span>}
      {state.error && <span className="inv-rule-note is-bad">{state.error}</span>}
    </div>
  );
}

/** Copy another character's saved settings onto this one. */
export function CopySettings({ character, onCopied }: { character: string | null; onCopied: (msg: string) => void }) {
  const [chars, setChars] = useState<SettingsCharacter[]>([]);
  const [source, setSource] = useState('');
  const [withGuard, setWithGuard] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<SettingsCharacter[]>('/api/settings/characters')
      .then(setChars)
      .catch(e => reportClientError(e, { component: 'CopySettings.chars', silent: true }));
  }, [character]);

  const others = chars.filter(c => c.character !== character);
  if (!character || others.length === 0) return null;

  const copy = async () => {
    if (!source) return;
    const sections = withGuard ? ['items', 'guard.potion', 'guard.buff'] : ['items'];
    const what = withGuard ? '道具處置與守護設定' : '道具處置';
    if (!window.confirm(`把「${source}」的${what}複製到「${character}」？目前的設定會被蓋掉。`)) return;
    setBusy(true);
    try {
      const r = await post<CopySettingsResult>('/api/settings/copy', { source, target: character, sections });
      onCopied(r.copied ? `已從「${source}」複製${what}` : `「${source}」沒有可以複製的設定`);
    } catch (e) {
      reportClientError(e, { component: 'CopySettings.copy' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inv-copy">
      <select value={source} onChange={e => setSource(e.target.value)} aria-label="複製處置設定的來源角色">
        <option value="">從其他角色複製處置…</option>
        {others.map(c => <option key={c.character} value={c.character}>{c.character}</option>)}
      </select>
      <label><input type="checkbox" checked={withGuard} onChange={e => setWithGuard(e.target.checked)} />連守護設定</label>
      <button type="button" className="is-ghost" onClick={copy} disabled={!source || busy}>複製</button>
    </span>
  );
}
