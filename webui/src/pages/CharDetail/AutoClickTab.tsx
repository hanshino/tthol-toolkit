import { useState } from 'react';
import { post } from '../../api/client';
import { Panel } from '../../primitives';

export function AutoClickTab({ pid }: { pid: number }) {
  const [running, setRunning] = useState(false);

  const start = async () => {
    await post(`/api/characters/${pid}/autoclick/start`, { interval_seconds: 60, merchant_idx: 0 });
    setRunning(true);
  };
  const stop = async () => { await post(`/api/characters/${pid}/autoclick/stop`); setRunning(false); };

  return (
    <Panel title="輔助·召喚商人">
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="is-primary" onClick={start} disabled={running}>啟動</button>
        <button onClick={stop} disabled={!running}>停止</button>
        <span style={{ marginLeft: 12, color: running ? 'var(--tt-ok)' : 'var(--tt-mute)', letterSpacing: 2, fontSize: 12 }}>
          {running ? '執行中' : '未啟用'}
        </span>
      </div>
    </Panel>
  );
}
