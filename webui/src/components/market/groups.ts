import type { ItemMeta } from '../../api/types';

/**
 * Browse groups for the 市價 page. The items page's six categories put ~70% of
 * market items under 裝備, so gear is split by slot here, and the two big
 * non-gear piles (boxes / charms, 真元 / 魂石) get their own group.
 */
export type MarketGroup =
  | 'weapon' | 'armor' | 'orn' | 'mount' | 'outfit'
  | 'box' | 'soul' | 'book' | 'pet' | 'potion' | 'misc';

export const MARKET_GROUPS: { key: MarketGroup; label: string }[] = [
  { key: 'weapon', label: '武器' },
  { key: 'armor', label: '防具' },
  { key: 'orn', label: '飾品' },
  { key: 'mount', label: '座騎・背飾' },
  { key: 'outfit', label: '外裝' },
  { key: 'box', label: '寶箱・符' },
  { key: 'soul', label: '真元・魂石' },
  { key: 'book', label: '秘笈' },
  { key: 'pet', label: '寵物' },
  { key: 'potion', label: '藥品' },
  { key: 'misc', label: '其他' },
];

const WEAPONS = new Set(['劍', '刀', '雙劍', '棍', '法杖', '拂塵', '暗器', '手甲', '雙手刀', '匕首', '扇', '手套', '拳刃', '盾']);
const ARMOR = new Set(['帽', '衣', '鞋']);
const ORNAMENTS = new Set(['左飾', '中飾', '右飾']);

export function marketGroup(meta: ItemMeta | undefined): MarketGroup {
  if (!meta) return 'misc';
  const label = meta.type_label;
  switch (meta.category) {
    case 'gear':
      if (label.includes('外裝')) return 'outfit';
      if (WEAPONS.has(label)) return 'weapon';
      if (ARMOR.has(label)) return 'armor';
      if (ORNAMENTS.has(label)) return 'orn';
      if (label === '座騎' || label === '背飾') return 'mount';
      return 'misc';
    case 'book': return 'book';
    case 'pet': return 'pet';
    case 'potion': return 'potion';
    default:
      if (label === '寶箱') return 'box';
      if (label === '真元/魂石') return 'soul';
      return 'misc';
  }
}

/** Second-level chip label: the item's type, or 未分類 for types the DB has no name for. */
export function subLabel(meta: ItemMeta | undefined): string {
  return meta?.type_label || '未分類';
}
