import { useState } from 'react';
import { TopNav, type PageKey } from './components/TopNav';
import { useLiveChars } from './hooks/useLiveChars';
import { Dashboard } from './pages/Dashboard';
import { Treasury } from './pages/Treasury';
import { ThemeProvider } from './theme/ThemeProvider';
import type { CharacterRow } from './api/types';

export function App() {
  const [page, setPage] = useState<PageKey>('dashboard');
  const [, setSelected] = useState<CharacterRow | null>(null);
  const snap = useLiveChars();
  const linked = snap.chars.filter(c => c.link === 'ok').length;

  return (
    <ThemeProvider>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--tt-bg)', color: 'var(--tt-text)' }}>
        <TopNav page={page} onNav={(k) => { setPage(k); setSelected(null); }} linkedCount={linked} totalCount={snap.chars.length} />
        <main style={{ flex: 1 }}>
          {page === 'dashboard' && (
            <Dashboard chars={snap.chars} onPick={(c) => { setSelected(c); setPage('detail'); }} />
          )}
          {page === 'treasury'  && <Treasury />}
          {page === 'snapshots' && <div style={{ padding: 24 }}>Snapshots placeholder</div>}
          {page === 'detail'    && <div style={{ padding: 24 }}>CharDetail placeholder</div>}
        </main>
      </div>
    </ThemeProvider>
  );
}
