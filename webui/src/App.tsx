import { useState } from 'react';
import { TopNav, type PageKey } from './components/TopNav';
import { useLiveChars } from './hooks/useLiveChars';
import { Dashboard } from './pages/Dashboard';
import { Treasury } from './pages/Treasury';
import { Snapshots } from './pages/Snapshots';
import { CharDetail } from './pages/CharDetail';
import type { CharacterRow } from './api/types';

export function App() {
  const [page, setPage] = useState<PageKey>('dashboard');
  const [selectedPid, setSelectedPid] = useState<number | null>(null);
  const snap = useLiveChars();
  const linked = snap.chars.filter(c => c.link === 'ok').length;
  const liveSelected: CharacterRow | undefined =
    selectedPid !== null ? snap.chars.find(c => c.pid === selectedPid) : undefined;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--tt-bg)', color: 'var(--tt-text)' }}>
      <TopNav page={page} onNav={(k) => { setPage(k); setSelectedPid(null); }} linkedCount={linked} totalCount={snap.chars.length} />
      <main style={{ flex: 1 }}>
        {page === 'dashboard' && (
          <Dashboard chars={snap.chars} onPick={(c) => { setSelectedPid(c.pid); setPage('detail'); }} />
        )}
        {page === 'treasury'  && <Treasury />}
        {page === 'snapshots' && <Snapshots />}
        {page === 'detail' && liveSelected && (
          <CharDetail char={liveSelected} onBack={() => setPage('dashboard')} />
        )}
      </main>
    </div>
  );
}
