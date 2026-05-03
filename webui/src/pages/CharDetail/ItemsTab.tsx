import { useState } from 'react';
import { post } from '../../api/client';
import { Panel } from '../../primitives';

interface Item { item_id: number; name: string; quantity: number; source: 'inventory' | 'warehouse'; }

export function ItemsTab({ pid }: { pid: number }) {
  const [items, setItems] = useState<Item[]>([]);
  const [filter, setFilter] = useState<'all' | 'inventory' | 'warehouse'>('all');

  const scanInventory = () => post<Item[]>(`/api/characters/${pid}/inventory/scan`).then(items => setItems(prev => [...prev.filter(i => i.source !== 'inventory'), ...items]));
  const scanWarehouse = () => post<Item[]>(`/api/characters/${pid}/warehouse/scan`).then(items => setItems(prev => [...prev.filter(i => i.source !== 'warehouse'), ...items]));

  const visible = items.filter(i => filter === 'all' || i.source === filter);
  return (
    <Panel title="行囊 / 庫房">
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button className="is-primary" onClick={scanInventory}>掃描行囊</button>
        <button className="is-primary" onClick={scanWarehouse}>掃描庫房</button>
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
