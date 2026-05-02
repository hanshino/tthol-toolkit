import { useState } from 'react';
import type { CharacterRow } from '../../api/types';
import { LinkDot, Seal } from '../../primitives';
import { BodyTab } from './BodyTab';
import { ItemsTab } from './ItemsTab';
import { AutoClickTab } from './AutoClickTab';
import { MapAnalysis } from './MapAnalysis';

const TABS = [
  { k: 'body', n: '根脈' },
  { k: 'items', n: '行囊' },
  { k: 'autoclick', n: '輔助' },
  { k: 'maps', n: '行止' },
] as const;

type TabKey = typeof TABS[number]['k'];

export function CharDetail({ char, onBack }: { char: CharacterRow; onBack: () => void }) {
  const [tab, setTab] = useState<TabKey>('body');
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
        <button onClick={onBack} style={{ background: 'transparent', color: 'var(--tt-dim)' }}>← 返回</button>
        <Seal>{char.name[0]}</Seal>
        <div>
          <div style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 18 }}>{char.name}</div>
          <div style={{ fontSize: 12, color: 'var(--tt-dim)' }}>{char.sect} · pid {char.pid}</div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <LinkDot status={char.link} /> Lv {char.level}
        </div>
      </div>
      <nav style={{ display: 'flex', gap: 0, marginBottom: 12 }}>
        {TABS.map(t => (
          <button key={t.k} onClick={() => setTab(t.k)} style={{
            padding: '6px 18px', background: tab === t.k ? 'var(--tt-raised)' : 'transparent',
            color: 'var(--tt-text)', border: '1px solid var(--tt-line)', borderBottom: 'none',
            fontFamily: 'var(--tt-font-serif)', letterSpacing: 4, cursor: 'pointer',
          }}>{t.n}</button>
        ))}
      </nav>
      {tab === 'body' && <BodyTab pid={char.pid} />}
      {tab === 'items' && <ItemsTab pid={char.pid} />}
      {tab === 'autoclick' && <AutoClickTab pid={char.pid} />}
      {tab === 'maps' && <MapAnalysis />}
    </div>
  );
}
