import { useEffect, useState } from 'react';
import { ApiError, get, post } from '../../api/client';
import type { CharacterRow, MarketListing, WalkStatus } from '../../api/types';
import { reportClientError } from '../../diag/report';
import { WALK_FAILURES } from '../walk/messages';

type GotoResult = { goal?: { x: number; y: number } | null; walk?: WalkStatus | null; note?: string | null };

const POLL_MS = 400;

/**
 * 帶我去: walk a character that is on the listing's map to the tile beside the
 * stall (services/market_goto.py picks it). Only same-map walks: cross-map
 * routing is not built yet.
 */
export function GotoStall({ listing, chars }: { listing: MarketListing; chars: CharacterRow[] }) {
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
      reportClientError(e, { component: 'GotoStall.go', silent: e instanceof ApiError && e.status < 500 });
    } finally { setBusy(false); }
  };
  const stop = () => {
    if (!chosen) return;
    post<WalkStatus>(`/api/characters/${chosen.pid}/walk/stop`).then(setWalk).catch(() => { /* the poll will tell */ });
  };

  const status = walk && (
    walk.state === 'walking' ? `走路中… 前往 (${goal?.x}, ${goal?.y})`
      : walk.state === 'done' ? `已到達攤位附近 (${goal?.x}, ${goal?.y})`
        : walk.state === 'idle' ? null
          : WALK_FAILURES[walk.message ?? ''] ?? walk.message ?? '已停止'
  );

  return (
    <div className="inv-d-sec">
      <span className="inv-d-label">帶我去</span>
      {here.length === 0
        ? <span className="mk-attr">沒有角色在{listing.map || '那張地圖'}；先讓角色到那裡</span>
        : (
          <div className="mk-goto">
            {here.length > 1 && (
              <select aria-label="要走路的角色" value={chosen?.pid} onChange={e => setPid(Number(e.target.value))} disabled={walking}>
                {here.map(c => <option key={c.pid} value={c.pid}>{c.name}</option>)}
              </select>
            )}
            {walking
              ? <button type="button" onClick={stop}>停止</button>
              : <button type="button" className="is-primary" disabled={busy} onClick={go}>
                  {here.length === 1 ? `讓 ${chosen?.name} 走過去` : '走過去'}
                </button>}
          </div>
        )}
      {listing.ended_at && <span className="mk-attr" style={{ color: 'var(--tt-warn)' }}>上次重開時這筆已經不在了，攤位可能已收攤</span>}
      {(status || text) && (
        <span className="mk-attr" data-tone={walk?.state === 'failed' ? 'bad' : walk?.state === 'done' ? 'ok' : undefined} aria-live="polite">
          {status ?? text}
        </span>
      )}
    </div>
  );
}
