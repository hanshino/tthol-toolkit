import { useState } from 'react';
import type { ItemMeta } from '../../api/types';
import { categoryColor, durationText, SOURCE_LABEL, SOURCE_SHORT, type Entry } from './entries';

export function ItemIcon({ name, meta, size }: { name: string; meta: ItemMeta | undefined; size: number }) {
  const [broken, setBroken] = useState(false);
  const url = meta?.icon_url;
  if (!url || broken) {
    // No icon in the DB (or the image host is unreachable): first character of the name.
    return <span className="inv-icon-fallback" aria-hidden="true">{name.slice(0, 1)}</span>;
  }
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      draggable={false}
      style={{ maxWidth: size, maxHeight: size }}
      onError={() => setBroken(true)}
    />
  );
}

function qtyText(n: number) {
  return n >= 100000 ? `${Math.floor(n / 10000)}萬` : String(n);
}

/** 道具處置 badge text, e.g. 定 / 解 / 賣 / 存; absent for no rule. */
export type RuleBadge = { short: string; label: string; tone: 'use' | 'later' };

type CellProps = {
  entry: Entry;
  selected: boolean;
  picked?: boolean;
  showSources: boolean;
  onSelect: (e: React.MouseEvent) => void;
  onMenu?: (e: React.MouseEvent) => void;
  rule?: RuleBadge;
};

export function Slot({ entry, selected, picked, showSources, onSelect, onMenu, rule }: CellProps) {
  return (
    <button
      type="button"
      className={picked ? 'inv-slot is-picked' : 'inv-slot'}
      aria-pressed={selected || !!picked}
      aria-label={`${entry.name} ×${entry.qty}${rule ? `，處置：${rule.label}` : ''}`}
      title={`${entry.name} ×${entry.qty.toLocaleString()}${rule ? `（${rule.label}）` : ''}`}
      style={{ '--inv-c': categoryColor(entry.category) } as React.CSSProperties}
      onClick={onSelect}
      onContextMenu={onMenu}
    >
      <ItemIcon name={entry.name} meta={entry.meta} size={44} />
      {entry.meta?.no_trade && <span className="inv-bound" />}
      {showSources
        ? <span className="inv-corner">{entry.sources.map(s => SOURCE_SHORT[s]).join('')}</span>
        : entry.stacks > 1 && <span className="inv-corner">{entry.stacks}格</span>}
      {entry.qty > 1 && <span className="inv-qty">{qtyText(entry.qty)}</span>}
      {rule && <span className={`inv-rule-badge is-${rule.tone}`} aria-hidden="true">{rule.short}</span>}
    </button>
  );
}

export function Row({ entry, selected, picked, showSources, onSelect, onMenu, rule }: CellProps) {
  const m = entry.meta;
  const sub = [
    m?.type_label,
    m?.level ? `Lv.${m.level}` : '',
    m?.effect_seconds ? `效果 ${durationText(m.effect_seconds)}` : '',
    m?.no_trade ? '不可交易' : '',
  ].filter(Boolean).join(' · ');
  const where = showSources
    ? entry.sources.map(s => SOURCE_LABEL[s]).join('、')
    : entry.stacks > 1 ? `${entry.stacks} 格` : '';
  return (
    <button type="button" className={picked ? 'inv-row is-picked' : 'inv-row'}
      aria-pressed={selected || !!picked} onClick={onSelect} onContextMenu={onMenu}>
      <span className="inv-row-icon">
        <ItemIcon name={entry.name} meta={m} size={36} />
        {m?.no_trade && <span className="inv-bound" />}
      </span>
      <span className="inv-row-name">
        <span>
          {entry.name}
          {rule && <span className={`inv-rule-pill is-${rule.tone}`}>{rule.label}</span>}
        </span>
        {sub && <span className="inv-row-sub">{sub}</span>}
      </span>
      <span className="inv-row-qty">
        ×{entry.qty.toLocaleString()}
        {where && <small>{where}</small>}
      </span>
    </button>
  );
}
