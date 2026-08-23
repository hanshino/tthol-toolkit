import { useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import { reportClientError } from '../../diag/report';
import { Panel } from '../../primitives';

type Status = {
  running: boolean;
  started_at?: number | null;
  runtime_seconds?: number | null;
  last_click_at?: number | null;
};

const MERCHANTS = [1, 2, 3, 4, 5];

type Mode = 'off' | 'collect' | 'destroy';

export function AutoClickTab({ pid }: { pid: number }) {
  const [merchantIdx, setMerchantIdx] = useState(0);
  const [intervalMs, setIntervalMs] = useState(500);
  const [mode, setMode] = useState<Mode>('off');
  const [clicksPerRound, setClicksPerRound] = useState(10);
  const [status, setStatus] = useState<Status>({ running: false });
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const s = await get<Status>(`/api/characters/${pid}/autoclick/status`);
      setStatus(s);
    } catch (e) {
      // Worker may be gone: intentionally not surfaced, but still recorded so
      // the timeline is complete.
      reportClientError(e, { component: 'AutoClickTab.refresh', silent: true });
    }
  };

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 2000);
    return () => window.clearInterval(t);
  }, [pid]);

  const start = async () => {
    setBusy('start');
    try {
      await post(`/api/characters/${pid}/autoclick/start`, {
        interval_ms: intervalMs,
        merchant_idx: merchantIdx,
        mode,
        clicks_per_round: clicksPerRound,
      });
      await refresh();
    } finally { setBusy(null); }
  };
  const stop = async () => {
    setBusy('stop');
    try {
      await post(`/api/characters/${pid}/autoclick/stop`);
      await refresh();
    } finally { setBusy(null); }
  };
  const test = async () => {
    setBusy('test');
    try {
      await post(`/api/characters/${pid}/autoclick/test`, { merchant_idx: merchantIdx });
    } finally { setBusy(null); }
  };

  const lastClick = status.last_click_at
    ? new Date(status.last_click_at * 1000).toLocaleTimeString('zh-TW', { hour12: false })
    : '—';

  return (
    <Panel title="輔助·召喚商人">
      <div style={{ display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--tt-dim)' }}>
            商人
            <select
              value={merchantIdx}
              onChange={e => setMerchantIdx(Number(e.target.value))}
              disabled={status.running}
              style={{ padding: '4px 8px' }}
            >
              {MERCHANTS.map((n, i) => <option key={i} value={i}>商人 {n}</option>)}
            </select>
          </label>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--tt-dim)' }}>
            間隔
            <input
              type="number"
              min={50}
              step={50}
              value={intervalMs}
              onChange={e => setIntervalMs(Math.max(50, Number(e.target.value) || 50))}
              disabled={status.running}
              style={{ width: 72, padding: '4px 8px' }}
            />
            <span style={{ color: 'var(--tt-mute)' }}>ms</span>
          </label>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--tt-dim)' }}>
            模式
            <select
              value={mode}
              onChange={e => setMode(e.target.value as Mode)}
              disabled={status.running}
              style={{ padding: '4px 8px' }}
            >
              <option value="off">純召喚</option>
              <option value="collect">全部收下（收完銷毀剩餘）</option>
              <option value="destroy">全部銷毀</option>
            </select>
          </label>
          <label
            style={{
              display: 'flex', gap: 6, alignItems: 'center', fontSize: 12, color: 'var(--tt-dim)',
              opacity: mode === 'off' ? 0.5 : 1,
            }}
            title="每召喚 N 次後按一次 全部收下/全部銷毀"
          >
            每輪召喚
            <input
              type="number"
              min={1}
              value={clicksPerRound}
              onChange={e => setClicksPerRound(Math.max(1, Number(e.target.value) || 1))}
              disabled={status.running || mode === 'off'}
              style={{ width: 56, padding: '4px 8px' }}
            />
            <span style={{ color: 'var(--tt-mute)' }}>次</span>
          </label>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="is-primary" onClick={start} disabled={status.running || busy !== null}>啟動</button>
          <button onClick={stop} disabled={!status.running || busy !== null}>停止</button>
          <button onClick={test} disabled={busy !== null} title="對選中商人發一次點擊">測試點擊</button>
          <span style={{
            marginLeft: 8,
            color: status.running ? 'var(--tt-ok)' : 'var(--tt-mute)',
            letterSpacing: 2, fontSize: 12,
          }}>
            {status.running ? `執行中 · ${status.runtime_seconds ?? 0}s` : '未啟用'}
          </span>
          <span style={{ color: 'var(--tt-mute)', fontSize: 11, marginLeft: 4 }}>
            上次點擊 {lastClick}
          </span>
        </div>
      </div>
    </Panel>
  );
}
