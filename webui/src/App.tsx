import { useCallback, useRef, useState } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Sidebar } from './components/Sidebar';
import { useLiveChars } from './hooks/useLiveChars';
import type { CharTab, GlobalView, OpenChar, View } from './nav';
import { Dashboard } from './pages/Dashboard';
import { Treasury } from './pages/Treasury';
import { Snapshots } from './pages/Snapshots';
import { Diagnostics } from './pages/Diagnostics';
import { CharWorkspace } from './pages/CharWorkspace';
import type { CharacterRow } from './api/types';

export function App() {
  const [view, setView] = useState<View>({ kind: 'overview' });
  const [tabByPid, setTabByPid] = useState<Record<number, CharTab>>({});
  const snap = useLiveChars();

  const nav = useCallback((k: GlobalView) => setView({ kind: k }), []);
  const openChar = useCallback<OpenChar>((pid, tab) => {
    if (tab) setTabByPid(m => ({ ...m, [pid]: tab }));
    setView({ kind: 'char', pid });
  }, []);

  // Last row seen per pid and when. A closed game drops its row from the
  // snapshot (WorkerManager.world_snapshot), so a selected pid that vanishes
  // renders from here with a 斷 banner instead of a blank main area.
  const lastSeen = useRef(new Map<number, { row: CharacterRow; at: number }>());
  for (const c of snap.chars) lastSeen.current.set(c.pid, { row: c, at: Date.now() });
  let workspace: { row: CharacterRow; goneSince: number | null } | null = null;
  if (view.kind === 'char') {
    const live = snap.chars.find(c => c.pid === view.pid);
    const seen = lastSeen.current.get(view.pid);
    if (live) workspace = { row: live, goneSince: null };
    else if (seen) workspace = { row: seen.row, goneSince: seen.at };
  }
  // Keyed per view so navigating away from a crashed view clears the fallback.
  const boundaryKey = view.kind === 'char' ? `char-${view.pid}` : view.kind;

  return (
    <div className="app-shell">
      <Sidebar view={view} chars={snap.chars} onNav={nav} onOpenChar={openChar} />
      <main className="app-main">
        <ErrorBoundary key={boundaryKey} component={boundaryKey}>
          {view.kind === 'overview' && <Dashboard chars={snap.chars} onOpenChar={openChar} />}
          {view.kind === 'treasury' && <Treasury />}
          {view.kind === 'snapshots' && <Snapshots />}
          {view.kind === 'diagnostics' && <Diagnostics />}
          {view.kind === 'char' && (workspace
            ? (
              <CharWorkspace
                key={view.pid}
                char={workspace.row}
                goneSince={workspace.goneSince}
                tab={tabByPid[view.pid] ?? 'items'}
                onTab={t => setTabByPid(m => ({ ...m, [view.pid]: t }))}
                onNav={nav}
              />
            )
            : <Dashboard chars={snap.chars} onOpenChar={openChar} />)}
        </ErrorBoundary>
      </main>
    </div>
  );
}
