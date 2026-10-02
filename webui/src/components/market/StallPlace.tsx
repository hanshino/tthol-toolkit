import { useEffect, useState } from 'react';
import { ApiError, get, post } from '../../api/client';
import type { CharacterRow, MarketListing, WalkStatus } from '../../api/types';
import { reportClientError } from '../../diag/report';
import { WALK_FAILURES } from '../walk/messages';

type GotoResult = { goal?: { x: number; y: number } | null; walk?: WalkStatus | null; note?: string | null };

const POLL_MS = 400;

/** Where the stall is, as one line. */
export function placeText(l: MarketListing): string {
  const map = l.map || '—';
  if (l.x != null && l.y != null) return `${map} (${l.x}, ${l.y})`;
  if (l.viewer_x != null && l.viewer_y != null) return `${map} · (${l.viewer_x}, ${l.viewer_y}) 附近`;
  return map;
}

/**
 * The stall's place with its 帶我去 action: a character on the listing's map
 * walks near the stall (services/market_goto.py picks the tile). Same map only;
 * cross-map routing is not built yet.
 */
export function StallPlace({ listing, chars }: { listing: MarketListing; chars: CharacterRow[] }) {
  const here = chars.filter(c => c.link === 'ok' && c.position.stage_id != null && c.position.stage_id === listing.stage_id);
  const [pid, setPid] = useState<number | null>(null);
  const [walk, setWalk] = useState<WalkStatus | null>(null);
  const [goal, setGoal] = useState<{ x: number; y: number } | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = here.find(c => c.pid === pid) ?? here[0];
  const walking = walk?.state === 'walking';

  useEffect(() => {
    if (!walking || !chosen) return;
    let misses = 0;
    const t = window.setInterval(() => {
      get<WalkStatus>(`/api/characters/${chosen.pid}/walk`)
        .then(w => { misses = 0; setWalk(w); })
        .catch(() => {
          misses += 1;
          if (misses >= 5) setWalk(w => ({ ...(w ?? { legs: 0 }), state: 'failed', message: 'lost contact' }));
        });
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [walking, chosen]);

  const go = async () => {
    if (!chosen) return;
    setBusy(true);
    setText(null);
    try {
      const r = await post<GotoResult>(`/api/market/listings/${listing.id}/goto`, { pid: chosen.pid });
      setGoal(r.goal ?? null);
      setWalk(r.walk ?? null);
      if (r.note === 'already there') setText('已經在攤位附近了');
      else if (r.note === 'viewer position') setText('沒有攤位的確切位置，改走到當時看到這攤的地方');
    } catch (e) {
      setWalk(null);
      setText(e instanceof ApiError && e.detail ? e.detail : '無法開始走路');
      reportClientError(e, { component: 'StallPlace.go', silent: e instanceof ApiError && e.status < 500 });
    } finally { setBusy(false); }
  };
  const stop = () => {
    if (!chosen) return;
    post<WalkStatus>(`/api/characters/${chosen.pid}/walk/stop`).then(setWalk).catch(() => { /* the poll will tell */ });
  };

  let status: string | null = text;
  let tone: 'ok' | 'bad' | undefined;
  if (walk && walk.state !== 'idle') {
    if (walk.state === 'walking') status = `${chosen?.name ?? ''} 走路中…`;
    else if (walk.state === 'done') { status = `已到達附近 (${goal?.x}, ${goal?.y})`; tone = 'ok'; }
    else { status = WALK_FAILURES[walk.message ?? ''] ?? walk.message ?? '已停止'; tone = 'bad'; }
  } else if (!status && here.length === 0) {
    status = `沒有角色在${listing.map || '這張地圖'}，帶我去要先到同一張地圖`;
  }

  return (
    <div className="mk-place">
      <div className="mk-place-line">
        <svg className="mk-pin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21z" /><circle cx="12" cy="9.5" r="2.5" />
        </svg>
        <span className="mk-place-text mk-mono">{placeText(listing)}</span>
        {here.length > 1 && !walking && (
          <select className="mk-go-who" aria-label="要走路的角色" value={chosen?.pid} onChange={e => setPid(Number(e.target.value))}>
            {here.map(c => <option key={c.pid} value={c.pid}>{c.name}</option>)}
          </select>
        )}
        {walking
          ? <button type="button" className="mk-go" onClick={stop}>停止</button>
          : (
            <button
              type="button" className="mk-go" disabled={busy || here.length === 0} onClick={go}
              title={chosen ? `讓 ${chosen.name} 走到這攤附近` : undefined}
            >
              帶我去
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
            </button>
          )}
      </div>
      {status && <span className="mk-place-status" data-tone={tone} aria-live="polite">{status}</span>}
    </div>
  );
}
