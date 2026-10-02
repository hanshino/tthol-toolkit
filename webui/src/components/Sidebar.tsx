import { useEffect, useState } from 'react';
import { get } from '../api/client';
import type { CharacterRow, DiagSummary } from '../api/types';
import { isStopped, isUnlocated, type GlobalView, type View } from '../nav';
import { LinkDot, Seal } from '../primitives';
import './sidebar.css';

const GLOBAL_NAV: { k: GlobalView; n: string; s: string }[] = [
  { k: 'overview', n: '總覽', s: '全角色一覽' },
  { k: 'treasury', n: '帳房', s: '全角色道具' },
  { k: 'market', n: '市價', s: '攤位行情' },
  { k: 'snapshots', n: '留影', s: '背包快照' },
];

function charMeta(c: CharacterRow): string {
  if (isUnlocated(c)) {
    return c.last_error?.code === 'E_LOCATE_EXHAUSTED' ? `pid ${c.pid} · 定位失敗` : `pid ${c.pid} · 連線中`;
  }
  if (isStopped(c)) return `Lv ${c.level} · 偵測已停止`;
  return `Lv ${c.level} · ${c.position.map_name ?? '—'}`;
}

export function Sidebar({
  view, chars, onNav, onOpenChar,
}: {
  view: View; chars: CharacterRow[];
  onNav: (k: GlobalView) => void; onOpenChar: (pid: number) => void;
}) {
  // The version used to be hardcoded in the header and drifted five releases
  // behind, so users reported a version that no longer existed.
  const [version, setVersion] = useState('');
  useEffect(() => {
    get<DiagSummary>('/api/diagnostics/summary')
      .then(s => setVersion(String((s.environment as Record<string, unknown>).app_version ?? '')))
      .catch(() => { /* cosmetic only; 脈案 reports the real failure */ });
  }, []);
  const linked = chars.filter(c => c.link === 'ok').length;
  const current = (k: GlobalView) => (view.kind === k ? 'page' : undefined);

  return (
    <aside className="sb">
      <div className="sb-brand">
        <Seal size={30}>御</Seal>
        <div>
          <div className="sb-title">御心鑒</div>
          <div className="sb-tag">tthol memory reader</div>
        </div>
      </div>
      <nav className="sb-nav" aria-label="全域">
        {GLOBAL_NAV.map(n => (
          <button key={n.k} type="button" className="sb-item" aria-current={current(n.k)} onClick={() => onNav(n.k)}>
            <span className="sb-item-n">{n.n}</span>
            <span className="sb-item-s">{n.s}</span>
          </button>
        ))}
      </nav>
      <div className="sb-sec">
        <span>角色</span>
        <span className="sb-mono">{linked}/{chars.length} 已連</span>
      </div>
      <nav className="sb-chars" aria-label="角色">
        {chars.length === 0 && <div className="sb-empty">尚未偵測到遊戲程式 — 開啟遊戲後會自動出現在這裡</div>}
        {chars.map(c => (
          <button
            key={c.pid}
            type="button"
            className="sb-char"
            aria-current={view.kind === 'char' && view.pid === c.pid ? 'page' : undefined}
            data-stale={isStopped(c) || undefined}
            title={`${c.name} · pid ${c.pid}`}
            onClick={() => onOpenChar(c.pid)}
          >
            <LinkDot status={c.link} />
            <span className="sb-char-text">
              <span className="sb-char-name">{c.name}</span>
              <span className="sb-char-meta">{charMeta(c)}</span>
            </span>
          </button>
        ))}
      </nav>
      <div className="sb-foot">
        <button type="button" className="sb-item sb-diag" aria-current={current('diagnostics')} onClick={() => onNav('diagnostics')}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
          </svg>
          <span className="sb-item-n">脈案</span>
          <span className="sb-item-s">診斷</span>
        </button>
        <span className="sb-mono sb-ver">{version ? `v${version}` : ''}</span>
      </div>
    </aside>
  );
}
