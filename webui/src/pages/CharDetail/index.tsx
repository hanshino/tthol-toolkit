import { useState } from 'react';
import type { CharacterRow } from '../../api/types';
import { LinkDot, Seal } from '../../primitives';
import { BodyTab } from './BodyTab';
import { ItemsTab } from './ItemsTab';
import { AutoClickTab } from './AutoClickTab';
import { KeepActiveTab } from './KeepActiveTab';
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
        <button className="is-ghost" onClick={onBack}>← 返回</button>
        <Seal>{char.name[0]}</Seal>
        <div>
          <div style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 18 }}>{char.name}</div>
          <div style={{ fontSize: 12, color: 'var(--tt-dim)' }}>{char.sect} · pid {char.pid}</div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <LinkDot status={char.link} /> Lv {char.level}
        </div>
      </div>
      <nav style={{
        display: 'flex', gap: 4, marginBottom: 12,
        borderBottom: '1px solid var(--tt-line)',
      }}>
        {TABS.map(t => {
          const active = tab === t.k;
          return (
            <button
              key={t.k}
              onClick={() => setTab(t.k)}
              className="is-ghost"
              style={{
                padding: '8px 22px',
                fontFamily: 'var(--tt-font-serif)',
                fontSize: 14,
                letterSpacing: 4,
                color: active ? 'var(--tt-gold)' : 'var(--tt-dim)',
                borderBottom: active ? '2px solid var(--tt-gold)' : '2px solid transparent',
                marginBottom: -1,
              }}
            >{t.n}</button>
          );
        })}
      </nav>
      {tab === 'body' && <BodyTab pid={char.pid} />}
      {tab === 'items' && <ItemsTab pid={char.pid} />}
      {tab === 'autoclick' && (
        <div style={{ display: 'grid', gap: 12 }}>
          <AutoClickTab pid={char.pid} />
          <KeepActiveTab pid={char.pid} />
        </div>
      )}
      {tab === 'maps' && <MapAnalysis char={char} />}
    </div>
  );
}
