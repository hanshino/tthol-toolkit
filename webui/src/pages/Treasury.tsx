import { useEffect, useState } from 'react';
import { get } from '../api/client';
import { Panel, StatNum } from '../primitives';

interface TreasurySummary { total_kinds: number; total_qty: number; on_person: number; week_delta: number; }

export function Treasury() {
  const [summary, setSummary] = useState<TreasurySummary>({ total_kinds: 0, total_qty: 0, on_person: 0, week_delta: 0 });
  const [search, setSearch] = useState('');

  useEffect(() => {
    // Until real /api/treasury/summary exists, derive from /api/snapshots count
    get<unknown[]>('/api/snapshots').then(rows => {
      setSummary({
        total_kinds: rows.length, total_qty: 0, on_person: 0, week_delta: 0,
      });
    }).catch(() => {});
  }, []);

  return (
    <div style={{ padding: 16 }}>
      <Panel title="帳房">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr) auto', gap: 24, alignItems: 'center', marginBottom: 16 }}>
          <Stat label="種類數" value={summary.total_kinds} />
          <Stat label="件數總計" value={summary.total_qty} />
          <Stat label="隨身可用" value={summary.on_person} />
          <Stat label="七日進出" value={summary.week_delta} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜尋道具…"
            style={{
              background: 'var(--tt-bg)', color: 'var(--tt-text)',
              border: '1px solid var(--tt-line)', padding: '6px 10px',
              fontFamily: 'var(--tt-font-mono)',
            }}
          />
        </div>
        <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>
          道具列表 / 持有者明細 — 連接真實資料後填入。
        </div>
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
