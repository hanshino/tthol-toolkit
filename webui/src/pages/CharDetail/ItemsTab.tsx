import { useState } from 'react';
import { post } from '../../api/client';
import type { Item } from '../../api/types';
import { Panel } from '../../primitives';

type Source = Item['source'];

export function ItemsTab({ pid }: { pid: number }) {
  const [items, setItems] = useState<Item[]>([]);
  const [filter, setFilter] = useState<Source | 'all'>('all');

  const scan = (source: Source) =>
    post<Item[]>(`/api/characters/${pid}/${source}/scan`).then(fresh =>
      setItems(prev => [...prev.filter(i => i.source !== source), ...fresh]),
    );

  const visible = items.filter(i => filter === 'all' || i.source === filter);
  return (
    <Panel title="行囊 / 庫房">
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button className="is-primary" onClick={() => scan('inventory')}>掃描行囊</button>
        <button className="is-primary" onClick={() => scan('warehouse')}>掃描庫房</button>
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
      <div style={{ display: 'grid', gap: 4 }}>
        {visible.map(i => (
          <div key={`${i.source}-${i.item_id}`} style={{ display: 'flex', justifyContent: 'space-between', padding: 6, borderBottom: '1px solid var(--tt-line-soft)' }}>
            <span>{i.name}</span>
            <span style={{ fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-dim)' }}>×{i.quantity}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
