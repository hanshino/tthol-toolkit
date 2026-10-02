import type { Inlay, ItemStat } from '../../api/types';

type Priced = { price: number; price_kind: 'silver' | 'coin' | 'negotiate'; coins?: number; silver?: number | null };

export type PriceText = { main: string; sub?: string; kind: Priced['price_kind'] };

export function silverText(n: number): string {
  if (n >= 100_000_000 && n % 1_000_000 === 0) return `${n / 100_000_000} 億`;
  if (n >= 10_000 && n % 10_000 === 0) return `${(n / 10_000).toLocaleString()} 萬`;
  return n.toLocaleString();
}

/**
 * Stall price conventions (see services/market_db.classify_price): 99,999,999 is
 * "haggle by 飛鴿", 9999xxxx asks the tail in 百萬官幣, anything else is silver.
 */
export function priceText(p: Priced): PriceText {
  if (p.price_kind === 'negotiate') return { main: '議價', sub: '飛鴿談', kind: 'negotiate' };
  if (p.price_kind === 'coin') {
    return { main: `${p.coins} 百萬官幣`, sub: `≈ ${silverText(p.silver ?? 0)}銀兩`, kind: 'coin' };
  }
  return { main: p.price.toLocaleString(), kind: 'silver' };
}

export function attrText(plus: number, stats: ItemStat[], inlays: Inlay[], max = 4): string {
  const parts: string[] = [];
  if (plus) parts.push(`+${plus}`);
  parts.push(...stats.slice(0, max).map(s => `${s.label} ${s.value.toLocaleString()}`));
  if (stats.length > max) parts.push(`…另 ${stats.length - max} 項`);
  parts.push(...inlays.map(i => `${i.name}${i.count > 1 ? `×${i.count}` : ''}`));
  return parts.join(' · ');
}

export function agoText(t: number | null | undefined, now = Date.now() / 1000): string {
  if (!t) return '—';
  const s = Math.max(0, now - t);
  if (s < 60) return '剛剛';
  if (s < 3600) return `${Math.floor(s / 60)} 分前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小時前`;
  return dateText(t);
}

export function dateText(t: number): string {
  const d = new Date(t * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function clockText(t: number): string {
  return new Date(t * 1000).toLocaleTimeString('zh-TW', { hour12: false });
}
