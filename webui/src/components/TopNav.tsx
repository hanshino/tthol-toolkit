import { useEffect, useState } from 'react';
import { get } from '../api/client';
import { LinkDot, Seal } from '../primitives';
import type { DiagSummary } from '../api/types';

export type PageKey = 'dashboard' | 'treasury' | 'snapshots' | 'diagnostics' | 'detail';

export function TopNav({
  page, onNav, linkedCount, totalCount,
}: { page: PageKey; onNav: (k: PageKey) => void; linkedCount: number; totalCount: number }) {
  const tabs: { k: PageKey; n: string }[] = [
    { k: 'dashboard',   n: '江湖一覽' },
    { k: 'treasury',    n: '帳房' },
    { k: 'snapshots',   n: '留影' },
    { k: 'diagnostics', n: '脈案' },
  ];
  // The version used to be hardcoded here and had drifted five releases behind,
  // so users reading it off the UI reported a version that no longer existed.
  const [version, setVersion] = useState('');
  useEffect(() => {
    get<DiagSummary>('/api/diagnostics/summary')
      .then(s => setVersion(String((s.environment as Record<string, unknown>).app_version ?? '')))
      .catch(() => { /* header cosmetics only; 脈案 reports the real failure */ });
  }, []);
  const ts = new Date().toLocaleTimeString('zh-TW', { hour12: false });
  return (
    <header style={{
      display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center',
      padding: '10px 18px', borderBottom: '1px solid var(--tt-line)', background: 'var(--tt-panel)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <Seal size={32}>御</Seal>
        <div>
          <div style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 16, fontWeight: 600, letterSpacing: 4 }}>御心鑒</div>
          <div style={{ fontSize: 10, color: 'var(--tt-mute)', letterSpacing: 2 }}>
            tthol memory reader{version ? ` · v${version}` : ''}
          </div>
        </div>
      </div>
      <nav style={{ display: 'flex' }}>
        {tabs.map(t => {
          const active = page === t.k || (page === 'detail' && t.k === 'dashboard');
          return (
            <button key={t.k} onClick={() => onNav(t.k)} style={{
              padding: '8px 22px', fontFamily: 'var(--tt-font-serif)', fontSize: 14,
              letterSpacing: 4, fontWeight: 600,
              background: active ? 'var(--tt-bg)' : 'transparent',
              color: active ? 'var(--tt-text)' : 'var(--tt-dim)',
              borderTop: '1px solid ' + (active ? 'var(--tt-line)' : 'transparent'),
              borderLeft: '1px solid ' + (active ? 'var(--tt-line)' : 'transparent'),
              borderRight: '1px solid ' + (active ? 'var(--tt-line)' : 'transparent'),
              borderBottom: 'none', cursor: 'pointer',
            }}>{t.n}</button>
          );
        })}
      </nav>
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 14, fontSize: 11, color: 'var(--tt-dim)', fontFamily: 'var(--tt-font-mono)' }}>
        <span><LinkDot status="ok" size={6} /> {linkedCount}/{totalCount} 已連</span>
        <span style={{ color: 'var(--tt-mute)' }}>{ts}</span>
      </div>
    </header>
  );
}
