import { useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import type { CharacterDetail, Item, SaveSnapshotResult } from '../../api/types';
import { Panel } from '../../primitives';

type Source = Item['source'];

export function ItemsTab({ pid }: { pid: number }) {
  const [items, setItems] = useState<Item[]>([]);
  const [filter, setFilter] = useState<Source | 'all'>('all');
  const [scanning, setScanning] = useState<Source | null>(null);
  const [saving, setSaving] = useState<Source | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    get<CharacterDetail>(`/api/characters/${pid}`)
      .then(d => {
        if (cancelled) return;
        const cached = [...(d.inventory ?? []), ...(d.warehouse ?? [])];
        if (cached.length) setItems(cached);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pid]);

  const scan = async (source: Source) => {
    if (scanning) return;
    setScanning(source);
    setToast(null);
    try {
      const fresh = await post<Item[]>(`/api/characters/${pid}/${source}/scan`);
      setItems(prev => [...prev.filter(i => i.source !== source), ...fresh]);
    } catch (e) {
      setToast(`掃描失敗：${String(e)}`);
    } finally {
      setScanning(null);
    }
  };

  const saveSnapshot = async (source: Source) => {
    if (saving) return;
    setSaving(source);
    setToast(null);
    try {
      const r = await post<SaveSnapshotResult>('/api/snapshots', { pid, source });
      setToast(r.saved ? '已存入留影' : '無新內容可存（可能與最近一筆相同）');
    } catch (e) {
      setToast(`保存失敗：${String(e)}`);
    } finally {
      setSaving(null);
    }
  };

  const visible = items.filter(i => filter === 'all' || i.source === filter);
  const hasInventory = items.some(i => i.source === 'inventory');
  const hasWarehouse = items.some(i => i.source === 'warehouse');
  return (
    <Panel title="行囊 / 庫房">
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <button
          className="is-primary"
          onClick={() => scan('inventory')}
          disabled={scanning !== null}
        >
          {scanning === 'inventory' ? '掃描中…' : '掃描行囊'}
        </button>
        <button
          className="is-primary"
          onClick={() => scan('warehouse')}
          disabled={scanning !== null}
        >
          {scanning === 'warehouse' ? '掃描中…' : '掃描庫房'}
        </button>
        <button
          className="is-ghost"
          onClick={() => saveSnapshot('inventory')}
          disabled={!hasInventory || saving !== null}
          title="將目前行囊內容存入留影"
        >
          {saving === 'inventory' ? '保存中…' : '↧ 留影身'}
        </button>
        <button
          className="is-ghost"
          onClick={() => saveSnapshot('warehouse')}
          disabled={!hasWarehouse || saving !== null}
          title="將目前庫房內容存入留影"
        >
          {saving === 'warehouse' ? '保存中…' : '↧ 留影庫'}
        </button>
        <span style={{ flex: 1 }} />
        {(['all', 'inventory', 'warehouse'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={filter === f ? 'is-active' : ''}
          >
            {f === 'all' ? '全部' : f === 'inventory' ? '身' : '庫'}
          </button>
        ))}
      </div>
      {toast && (
        <div style={{
          padding: '6px 10px', marginBottom: 8, fontSize: 12,
          background: 'var(--tt-raised)', border: '1px solid var(--tt-line-soft)',
          color: 'var(--tt-dim)',
        }}>{toast}</div>
      )}
      <div style={{ display: 'grid', gap: 4 }}>
        {visible.map(i => (
          <div key={`${i.source}-${i.item_id}`} style={{ display: 'flex', justifyContent: 'space-between', padding: 6, borderBottom: '1px solid var(--tt-line-soft)' }}>
            <span>{i.name}</span>
            <span style={{ fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-dim)' }}>×{i.quantity}</span>
          </div>
        ))}
        {visible.length === 0 && (
          <div style={{ color: 'var(--tt-mute)', fontSize: 12, padding: 12 }}>
            尚無資料 — 點擊上方「掃描行囊」或「掃描庫房」開始
          </div>
        )}
      </div>
    </Panel>
  );
}
