import { useEffect, useState } from 'react';
import { get } from '../api/client';
import type { SnapshotRow } from '../api/types';
import { Panel } from '../primitives';

export function Snapshots() {
  const [rows, setRows] = useState<SnapshotRow[]>([]);
  const [selected, setSelected] = useState<SnapshotRow | null>(null);

  useEffect(() => { get<SnapshotRow[]>('/api/snapshots').then(setRows).catch(() => {}); }, []);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16, padding: 16 }}>
      <Panel title="留影列表">
        <div style={{ display: 'grid', gap: 4 }}>
          {rows.map(r => (
            <button
              key={r.snapshot_id}
              onClick={() => setSelected(r)}
              style={{
                textAlign: 'left', padding: 8, background: selected?.snapshot_id === r.snapshot_id ? 'var(--tt-raised)' : 'transparent',
                border: '1px solid var(--tt-line-soft)', color: 'var(--tt-text)', cursor: 'pointer',
              }}
            >
              <div style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 2 }}>{r.character_name}</div>
              <div style={{ fontSize: 11, color: 'var(--tt-mute)', fontFamily: 'var(--tt-font-mono)' }}>
                {r.saved_at} · {r.source} · {r.item_count} 件
              </div>
            </button>
          ))}
        </div>
      </Panel>
      <Panel title="留影內容">
        {selected ? (
          <div>
            <div style={{ marginBottom: 12 }}>
              <strong style={{ fontFamily: 'var(--tt-font-serif)' }}>{selected.character_name}</strong>
              <span style={{ color: 'var(--tt-mute)', marginLeft: 8 }}>{selected.saved_at}</span>
            </div>
            <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>
              {selected.item_count} 件道具（道具明細 v1.1 接入；diff 已延後）
            </div>
          </div>
        ) : (
          <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>選擇一筆留影查看內容</div>
        )}
      </Panel>
    </div>
  );
}
