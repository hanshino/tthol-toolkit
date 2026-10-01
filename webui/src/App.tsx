import { useCallback, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Sidebar } from './components/Sidebar';
import { useLiveChars } from './hooks/useLiveChars';
import type { CharTab, GlobalView, OpenChar, View } from './nav';
import { Dashboard } from './pages/Dashboard';
import { Treasury } from './pages/Treasury';
import { Snapshots } from './pages/Snapshots';
import { Diagnostics } from './pages/Diagnostics';
import { CharDetail } from './pages/CharDetail';

export function App() {
  const [view, setView] = useState<View>({ kind: 'overview' });
  const [, setTabByPid] = useState<Record<number, CharTab>>({});
  const snap = useLiveChars();

  const nav = useCallback((k: GlobalView) => setView({ kind: k }), []);
  const openChar = useCallback<OpenChar>((pid, tab) => {
    if (tab) setTabByPid(m => ({ ...m, [pid]: tab }));
    setView({ kind: 'char', pid });
  }, []);

  const selected = view.kind === 'char' ? snap.chars.find(c => c.pid === view.pid) : undefined;
  // Keyed per view so navigating away from a crashed view clears the fallback.
  const boundaryKey = view.kind === 'char' ? `char-${view.pid}` : view.kind;

  return (
    <div className="app-shell">
      <Sidebar view={view} chars={snap.chars} onNav={nav} onOpenChar={openChar} />
      <main className="app-main">
        <ErrorBoundary key={boundaryKey} component={boundaryKey}>
          {view.kind === 'overview' && <Dashboard chars={snap.chars} onPick={c => openChar(c.pid)} />}
          {view.kind === 'treasury' && <Treasury />}
          {view.kind === 'snapshots' && <Snapshots />}
          {view.kind === 'diagnostics' && <Diagnostics />}
          {view.kind === 'char' && (selected
            ? <CharDetail char={selected} />
            : <Dashboard chars={snap.chars} onPick={c => openChar(c.pid)} />)}
        </ErrorBoundary>
      </main>
    </div>
  );
}
