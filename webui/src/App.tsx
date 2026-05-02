import { useState } from 'react';
import { TopNav, type PageKey } from './components/TopNav';
import { ThemeProvider } from './theme/ThemeProvider';

export function App() {
  const [page, setPage] = useState<PageKey>('dashboard');
  return (
    <ThemeProvider>
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--tt-bg)', color: 'var(--tt-text)' }}>
        <TopNav page={page} onNav={setPage} linkedCount={0} totalCount={0} />
        <main style={{ flex: 1, padding: 24 }}>
          {page === 'dashboard' && <div>Dashboard placeholder</div>}
          {page === 'treasury'  && <div>Treasury placeholder</div>}
          {page === 'snapshots' && <div>Snapshots placeholder</div>}
        </main>
      </div>
    </ThemeProvider>
  );
}
