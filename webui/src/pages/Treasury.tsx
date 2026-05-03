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
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜尋道具…"
            style={{ minWidth: 200 }}
          />
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

function ItemRow({ item }: { item: TreasuryItem }) {
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
      <span style={{ color: 'var(--tt-dim)', fontSize: 11 }}>
        {item.holders.map((h, i) => (
          <span key={`${h.character}-${h.source}-${i}`} style={{ marginRight: 8 }}>
            {h.character}
            <span style={{ color: 'var(--tt-mute)' }}>·{h.source === 'warehouse' ? '庫' : '身'}×{h.qty}</span>
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
