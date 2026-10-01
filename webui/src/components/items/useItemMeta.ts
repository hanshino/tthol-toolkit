import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import type { ItemMeta } from '../../api/types';
import { reportClientError } from '../../diag/report';

// Item metadata is static (it comes from the bundled DB), so one cache for the
// whole page lifetime; every tab and character shares it.
const cache = new Map<number, ItemMeta>();
// Ids the DB does not know: remembered so they are not re-requested every poll.
const missing = new Set<number>();
const inflight = new Set<number>();

export function useItemMeta(ids: number[]): Map<number, ItemMeta> {
  const [, bump] = useState(0);
  const want = [...new Set(ids)].filter(id => !cache.has(id) && !missing.has(id) && !inflight.has(id));
  const key = want.sort((a, b) => a - b).join(',');

  useEffect(() => {
    if (!key) return;
    const batch = key.split(',').map(Number);
    batch.forEach(id => inflight.add(id));
    let alive = true;
    get<ItemMeta[]>(`/api/items?ids=${key}`)
      .then(metas => {
        metas.forEach(m => cache.set(m.item_id, m));
        batch.forEach(id => { if (!cache.has(id)) missing.add(id); });
      })
      .catch(e => reportClientError(e, { component: 'useItemMeta' }))
      .finally(() => {
        batch.forEach(id => inflight.delete(id));
        if (alive) bump(n => n + 1);
      });
    return () => { alive = false; };
  }, [key]);

  return cache;
}
