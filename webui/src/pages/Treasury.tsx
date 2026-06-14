import { useEffect, useMemo, useState } from 'react';
import { get } from '../api/client';
import { Panel, StatNum } from '../primitives';
import type { TreasuryItem, TreasurySummary } from '../api/types';

export function Treasury() {
  const [summary, setSummary] = useState<TreasurySummary>({
    total_kinds: 0, total_qty: 0, on_person: 0, in_warehouse: 0,
  });
  const [items, setItems] = useState<TreasuryItem[]>([]);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      get<TreasurySummary>('/api/treasury/summary'),
      get<TreasuryItem[]>('/api/treasury/items'),
    ])
      .then(([s, list]) => {
        if (cancelled) return;
        setSummary(s);
        setItems(list);
        setError(null);
      })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return items;
    return items.filter(i =>
      i.name.toLowerCase().includes(needle) || i.item_type.toLowerCase().includes(needle),
    );
  }, [items, search]);

  return (
    <div style={{ padding: 16 }}>
      <Panel title="帳房">
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr) auto',
          gap: 24, alignItems: 'center', marginBottom: 16,
        }}>
          <Stat label="種類數"   value={summary.total_kinds} />
          <Stat label="件數總計" value={summary.total_qty} />
          <Stat label="隨身可用" value={summary.on_person} />
          <Stat label="庫房存放" value={summary.in_warehouse} />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'flex-end' }}>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜尋道具…"
              style={{ minWidth: 200 }}
            />
            <ExportMenu />
          </div>
        </div>
        {error && <Empty text={`讀取失敗：${error}`} />}
        {!error && loading && <Empty text="讀取中…" />}
        {!error && !loading && filtered.length === 0 && (
          <Empty text={search ? '查無符合的道具' : '尚無留影資料 — 請先到角色頁面儲存背包/庫房 snapshot'} />
        )}
        {!error && !loading && filtered.length > 0 && (
          <div style={{ display: 'grid', gap: 4 }}>
            <ItemHeader />
            {filtered.map(it => <ItemRow key={it.item_id} item={it} />)}
          </div>
        )}
      </Panel>
    </div>
  );
}

function ExportMenu() {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: 'relative' }}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        匯出報表 ▾
      </button>
      {open && (
        <>
          {/* click-away backdrop */}
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 20 }} />
          <div
            role="menu"
            style={{
              position: 'absolute', right: 0, top: 'calc(100% + 4px)', zIndex: 30,
              display: 'grid', gap: 4, padding: 4, minWidth: 150,
              background: 'var(--tt-panel)', border: '1px solid var(--tt-line)',
            }}
          >
            <a
              className="tt-btn" role="menuitem" download
              href="/api/treasury/export.csv?mode=detail"
              onClick={() => setOpen(false)}
            >
              明細 CSV
            </a>
            <a
              className="tt-btn" role="menuitem" download
              href="/api/treasury/export.csv?mode=summary"
              onClick={() => setOpen(false)}
            >
              彙總 CSV
            </a>
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--tt-mute)', letterSpacing: 2 }}>{label}</div>
      <div style={{ fontSize: 24 }}><StatNum value={value} /></div>
    </div>
  );
}

function ItemHeader() {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1.5fr 80px 60px 60px 60px 1.5fr',
      gap: 12, padding: '4px 8px',
      fontSize: 11, color: 'var(--tt-mute)', letterSpacing: 2,
      borderBottom: '1px solid var(--tt-line-soft)',
    }}>
      <span>名</span>
      <span>類型</span>
      <span style={{ textAlign: 'right' }}>身上</span>
      <span style={{ textAlign: 'right' }}>庫房</span>
      <span style={{ textAlign: 'right' }}>合計</span>
      <span>持有者</span>
    </div>
  );
}

type HolderGroup = { character: string; on_person: number; in_warehouse: number };

function groupHolders(holders: TreasuryItem['holders']): HolderGroup[] {
  const map = new Map<string, HolderGroup>();
  for (const h of holders) {
    let g = map.get(h.character);
    if (!g) {
      g = { character: h.character, on_person: 0, in_warehouse: 0 };
      map.set(h.character, g);
    }
    if (h.source === 'warehouse') g.in_warehouse += h.qty;
    else g.on_person += h.qty;
  }
  return [...map.values()].sort(
    (a, b) => (b.on_person + b.in_warehouse) - (a.on_person + a.in_warehouse),
  );
}

function ItemRow({ item }: { item: TreasuryItem }) {
  const groups = useMemo(() => groupHolders(item.holders), [item.holders]);
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1.5fr 80px 60px 60px 60px 1.5fr',
      gap: 12, padding: '6px 8px', fontSize: 12,
      borderBottom: '1px solid var(--tt-line-soft)',
      alignItems: 'baseline',
    }}>
      <span style={{ fontFamily: 'var(--tt-font-serif)' }}>{item.name}</span>
      <span style={{ color: 'var(--tt-dim)', fontSize: 11 }}>{item.item_type || '—'}</span>
      <span style={{ textAlign: 'right', fontFamily: 'var(--tt-font-mono)' }}>{item.on_person}</span>
      <span style={{ textAlign: 'right', fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-dim)' }}>{item.in_warehouse}</span>
      <span style={{ textAlign: 'right', fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-gold)' }}>{item.total_qty}</span>
      <span style={{ color: 'var(--tt-dim)', fontSize: 11, display: 'flex', flexWrap: 'wrap', gap: '2px 12px' }}>
        {groups.map((g) => (
          <span key={g.character} style={{ whiteSpace: 'nowrap' }}>
            {g.character}
            {g.on_person > 0 && (
              <span style={{ color: 'var(--tt-mute)' }}> 身×{g.on_person}</span>
            )}
            {g.in_warehouse > 0 && (
              <span style={{ color: 'var(--tt-mute)' }}> 庫×{g.in_warehouse}</span>
            )}
          </span>
        ))}
      </span>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div style={{ color: 'var(--tt-mute)', fontSize: 12, padding: 24, textAlign: 'center' }}>
      {text}
    </div>
  );
}
