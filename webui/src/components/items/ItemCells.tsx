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

export function Slot({
  entry, selected, showSources, onSelect,
}: { entry: Entry; selected: boolean; showSources: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      className="inv-slot"
      aria-pressed={selected}
      aria-label={`${entry.name} ×${entry.qty}`}
      title={`${entry.name} ×${entry.qty.toLocaleString()}`}
      style={{ '--inv-c': categoryColor(entry.category) } as React.CSSProperties}
      onClick={onSelect}
    >
      <ItemIcon name={entry.name} meta={entry.meta} size={44} />
      {entry.meta?.no_trade && <span className="inv-bound" />}
      {showSources
        ? <span className="inv-corner">{entry.sources.map(s => SOURCE_SHORT[s]).join('')}</span>
        : entry.stacks > 1 && <span className="inv-corner">{entry.stacks}格</span>}
      {entry.qty > 1 && <span className="inv-qty">{qtyText(entry.qty)}</span>}
    </button>
  );
}

export function Row({
  entry, selected, showSources, onSelect,
}: { entry: Entry; selected: boolean; showSources: boolean; onSelect: () => void }) {
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
    <button type="button" className="inv-row" aria-pressed={selected} onClick={onSelect}>
      <span className="inv-row-icon">
        <ItemIcon name={entry.name} meta={m} size={36} />
        {m?.no_trade && <span className="inv-bound" />}
      </span>
      <span className="inv-row-name">
        <span>{entry.name}</span>
        {sub && <span className="inv-row-sub">{sub}</span>}
      </span>
      <span className="inv-row-qty">
        ×{entry.qty.toLocaleString()}
        {where && <small>{where}</small>}
      </span>
    </button>
  );
}
