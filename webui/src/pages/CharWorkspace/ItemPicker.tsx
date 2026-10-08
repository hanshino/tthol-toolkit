import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import type { HandoffBagItem, ItemMeta } from '../../api/types';
import { reportClientError } from '../../diag/report';
import './handoff.css';

// Add items to a whitelist from a search, the bag or the warehouse (分身交貨,
// 領倉白名單). Each action is one way to add (存倉 / 留身上, or just 加入).
type Category = ItemMeta['category'];
const CATEGORIES: [Category | '', string][] = [
  ['', '全部類別'], ['book', '技能書'], ['gear', '裝備'], ['potion', '藥品'],
  ['event', '活動'], ['pet', '寵物'], ['misc', '其他'],
];
type Source = 'search' | 'bag' | 'warehouse';
type Pick = { item_id: number; name: string; note: string; warn: string | null };
export type PickAction = { key: string; label: string; bulk: string; className?: string };

const fmt = (n: number) => n.toLocaleString('en-US');

export function ItemPicker({
  bag, warehouse, taken, actions, onAdd, tradable = false, warnStore = false,
  warehouseEmpty = '還沒讀到倉庫：在遊戲裡打開一次這隻角色的倉庫（看一眼就好），再回來這裡。',
}: {
  bag: HandoffBagItem[]; warehouse: HandoffBagItem[]; taken: Set<number>;
  actions: PickAction[]; onAdd: (ids: number[], action: string) => void;
  tradable?: boolean;  // only tradable items (a trade whitelist)
  warnStore?: boolean;  // mark items the warehouse refuses
  warehouseEmpty?: string;
}) {
  const [source, setSource] = useState<Source | null>(null);
  const [q, setQ] = useState('');
  const [cat, setCat] = useState<Category | ''>('book');
  const [hits, setHits] = useState<ItemMeta[]>([]);

  useEffect(() => {
    if (source !== 'search' || (!q.trim() && !cat)) { setHits([]); return; }
    const t = window.setTimeout(async () => {
      try {
        const params = new URLSearchParams({ limit: '1000', q: q.trim() });
        if (tradable) params.set('tradable', 'true');
        if (cat) params.set('category', cat);
        setHits(await get<ItemMeta[]>(`/api/items/search?${params}`));
      } catch (e) {
        reportClientError(e, { component: 'ItemPicker.search', silent: true });
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [source, q, cat, tradable]);

  const SOURCES: [Source, string][] = [['search', '搜尋道具'], ['bag', `從背包挑（${bag.length}）`], ['warehouse', `從倉庫挑（${warehouse.length}）`]];
  if (source === null) {
    return (
      <div className="ho-add">
        {SOURCES.map(([s, label]) => (
          <button key={s} type="button" className="gd-add" onClick={() => setSource(s)}>＋ {label}</button>
        ))}
      </div>
    );
  }
  const f = q.trim();
  const fromHeld = (rows: HandoffBagItem[]): Pick[] => rows
    .filter(r => (!tradable || !r.no_trade) && (!cat || r.category === cat) && (!f || r.name.includes(f)))
    .map(r => ({
      item_id: r.item_id, name: r.name, note: `${fmt(r.count)}（${r.stacks} 格）`,
      warn: warnStore && r.no_store ? '不能存倉' : null,
    }));
  const rows: Pick[] = source === 'search'
    ? hits.map(h => ({ item_id: h.item_id, name: h.name, note: h.type_label, warn: warnStore && h.no_store ? '不能存倉' : null }))
    : fromHeld(source === 'bag' ? bag : warehouse);
  const fresh = rows.filter(r => !taken.has(r.item_id));
  const empty = source === 'search' && !f && !cat ? '輸入名稱，或選一個類別。'
    : source === 'warehouse' && warehouse.length === 0 ? warehouseEmpty
    : '沒有符合、還沒加入的道具。';
  return (
    <div className="gd-picker ho-picker">
      <div className="gd-picker-head ho-picker-head">
        <span className="sp-radio" role="radiogroup" aria-label="從哪裡挑">
          {SOURCES.map(([s, label]) => (
            <button key={s} type="button" role="radio" aria-checked={source === s}
              className={source === s ? 'is-active' : ''} onClick={() => setSource(s)}>{label}</button>
          ))}
        </span>
        <button type="button" onClick={() => setSource(null)}>關閉</button>
      </div>
      <div className="ho-picker-tools">
        <label className="sp-search">
          <span>名稱</span>
          <input type="text" value={q} autoFocus placeholder="例如 刀、11級" onChange={e => setQ(e.target.value)} />
        </label>
        <label className="sp-search">
          <span>類別</span>
          <select className="dl-select" value={cat} onChange={e => setCat(e.target.value as Category | '')}>
            {CATEGORIES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
        <span className="gd-dim">{fresh.length} 項可加入{rows.length > fresh.length ? `（另有 ${rows.length - fresh.length} 項已在名單）` : ''}</span>
        {fresh.length > 0 && (
          <span className="ho-bulk">
            {actions.map(a => (
              <button key={a.key} type="button" className={a.className}
                onClick={() => onAdd(fresh.map(r => r.item_id), a.key)}>{a.bulk}</button>
            ))}
          </span>
        )}
      </div>
      {fresh.length === 0 ? (
        <div className="gd-dim gd-pad">{empty}</div>
      ) : (
        <ul className="ho-pick-list" aria-label="可加入的道具">
          {fresh.map(r => (
            <li key={r.item_id}>
              <span className="ho-chip-name">{r.name}</span>
              <span className="gd-dim">{r.note}{r.warn ? `　${r.warn}` : ''}</span>
              {actions.map(a => (
                <button key={a.key} type="button" className={`ho-mini${a.className ? ` ${a.className}` : ''}`}
                  onClick={() => onAdd([r.item_id], a.key)}>{a.label}</button>
              ))}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
