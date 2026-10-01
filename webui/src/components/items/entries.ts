import type { Item, ItemMeta } from '../../api/types';

export type Source = Item['source'];
export type Category = ItemMeta['category'];
export type Tab = Source | 'all';

export const SOURCES: Source[] = ['inventory', 'pet', 'warehouse'];
export const SOURCE_LABEL: Record<Source, string> = { inventory: '行囊', pet: '寵物背包', warehouse: '庫房' };
export const SOURCE_SHORT: Record<Source, string> = { inventory: '身', pet: '寵', warehouse: '庫' };

export const CATEGORIES: { key: Category; label: string; color: string }[] = [
  { key: 'potion', label: '藥品', color: 'var(--inv-cat-potion)' },
  { key: 'gear', label: '裝備', color: 'var(--inv-cat-gear)' },
  { key: 'book', label: '秘笈', color: 'var(--inv-cat-book)' },
  { key: 'pet', label: '寵物', color: 'var(--inv-cat-pet)' },
  { key: 'event', label: '活動 / 商城', color: 'var(--inv-cat-event)' },
  { key: 'misc', label: '其他', color: 'var(--inv-cat-misc)' },
];

export function categoryColor(c: Category): string {
  return CATEGORIES.find(x => x.key === c)?.color ?? 'transparent';
}

/** One cell on screen: a single slot, or every slot of one item when merged. */
export interface Entry {
  key: string;
  itemId: number;
  name: string;
  qty: number;
  stacks: number;
  sources: Source[];
  meta: ItemMeta | undefined;
  category: Category;
}

export function buildEntries(
  slots: Item[],
  meta: Map<number, ItemMeta>,
  opts: { tab: Tab; merge: boolean; query: string },
): Entry[] {
  const q = opts.query.trim();
  const inTab = slots
    .map((s, idx) => ({ s, idx }))
    .filter(({ s }) => opts.tab === 'all' || s.source === opts.tab)
    .filter(({ s }) => {
      if (!q) return true;
      const m = meta.get(s.item_id);
      return s.name.includes(q) || (m?.description.includes(q) ?? false);
    });

  const entries: Entry[] = [];
  const byKey = new Map<string, Entry>();
  for (const { s, idx } of inTab) {
    // The same item can fill several slots (four stacks of one potion), so a
    // slot is keyed by position; a merged cell by item (and source, unless the
    // tab spans every container).
    const key = !opts.merge
      ? `${s.source}:${idx}`
      : opts.tab === 'all' ? `${s.item_id}` : `${s.source}:${s.item_id}`;
    const found = byKey.get(key);
    if (found) {
      found.qty += s.quantity;
      found.stacks += 1;
      if (!found.sources.includes(s.source)) found.sources.push(s.source);
      continue;
    }
    const m = meta.get(s.item_id);
    const e: Entry = {
      key,
      itemId: s.item_id,
      name: m?.name || s.name,
      qty: s.quantity,
      stacks: 1,
      sources: [s.source],
      meta: m,
      category: m?.category ?? 'misc',
    };
    byKey.set(key, e);
    entries.push(e);
  }
  return entries;
}

export function groupByCategory(entries: Entry[]) {
  return CATEGORIES
    .map(c => ({ ...c, entries: entries.filter(e => e.category === c.key) }))
    .filter(g => g.entries.length > 0);
}

export function holdings(slots: Item[], itemId: number): Record<Source, number> {
  const h: Record<Source, number> = { inventory: 0, pet: 0, warehouse: 0 };
  for (const s of slots) if (s.item_id === itemId) h[s.source] += s.quantity;
  return h;
}

export function durationText(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600} 小時`;
  if (seconds >= 60) return `${Math.round(seconds / 60)} 分鐘`;
  return `${seconds} 秒`;
}
