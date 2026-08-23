import { useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import { reportClientError } from '../../diag/report';
import { Panel } from '../../primitives';

type Status = {
  running: boolean;
  started_at?: number | null;
  runtime_seconds?: number | null;
  last_send_at?: number | null;
};

export function KeepActiveTab({ pid }: { pid: number }) {
  const [status, setStatus] = useState<Status>({ running: false });
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const s = await get<Status>(`/api/characters/${pid}/keep-active/status`);
      setStatus(s);
    } catch (e) {
      // Manager may be absent off-Windows: intentionally not surfaced, but
      // still recorded so the timeline is complete.
      reportClientError(e, { component: 'KeepActiveTab.refresh', silent: true });
    }
  };

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 2000);
    return () => window.clearInterval(t);
  }, [pid]);

  const start = async () => {
    setBusy('start');
    try { await post(`/api/characters/${pid}/keep-active/start`); await refresh(); }
    finally { setBusy(null); }
  };
  const stop = async () => {
    setBusy('stop');
    try { await post(`/api/characters/${pid}/keep-active/stop`); await refresh(); }
    finally { setBusy(null); }
  };

  const lastSend = status.last_send_at
    ? new Date(status.last_send_at * 1000).toLocaleTimeString('zh-TW', { hour12: false })
    : '—';

  return (
    <Panel title="輔助·保持視窗渲染">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="is-primary" onClick={start} disabled={status.running || busy !== null}>啟動</button>
        <button onClick={stop} disabled={!status.running || busy !== null}>停止</button>
        <span style={{
          marginLeft: 8,
          color: status.running ? 'var(--tt-ok)' : 'var(--tt-mute)',
          letterSpacing: 2, fontSize: 12,
        }}>
          {status.running ? `執行中 · ${status.runtime_seconds ?? 0}s` : '未啟用'}
        </span>
        <span style={{ color: 'var(--tt-mute)', fontSize: 11, marginLeft: 4 }}>
          上次補活化 {lastSend}
        </span>
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--tt-mute)', lineHeight: 1.6 }}>
        切到別的視窗時，遊戲常會停下渲染。啟動後，當前景視窗變動，會送一組假啟用訊息給遊戲視窗，讓畫面持續更新。
      </div>
    </Panel>
  );
}
