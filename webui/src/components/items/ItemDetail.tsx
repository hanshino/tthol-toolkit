import type { ReactNode } from 'react';
import type { Item } from '../../api/types';
import { ItemIcon } from './ItemCells';
import { categoryColor, durationText, holdings, SOURCE_LABEL, SOURCES, type Entry } from './entries';

/**
 * Side panel for one item. `children` is the "who holds it" section, which
 * differs per page: containers of one character, or characters in the treasury.
 */
export function ItemDetail({ entry, children }: { entry: Entry | undefined; children?: ReactNode }) {
  if (!entry) {
    return <aside className="inv-detail"><div className="inv-empty">點選道具查看詳情</div></aside>;
  }
  const m = entry.meta;
  const tags: { text: string; tone?: 'seal' | 'time' }[] = [];
  if (m?.no_trade) tags.push({ text: '不可交易', tone: 'seal' });
  if (m?.no_store) tags.push({ text: '不可存倉', tone: 'seal' });
  if (m?.no_drop) tags.push({ text: '不可丟棄' });
  if (m?.effect_seconds) tags.push({ text: `效果 ${durationText(m.effect_seconds)}`, tone: 'time' });

  return (
    <aside className="inv-detail" aria-live="polite">
      <div className="inv-d-head">
        <div className="inv-d-icon"><ItemIcon name={entry.name} meta={m} size={44} /></div>
        <div style={{ minWidth: 0 }}>
          <div className="inv-d-name">{entry.name}</div>
          <div className="inv-d-type">
            <span className="inv-cdot" style={{ background: categoryColor(entry.category) }} />
            {m?.type_label || '道具'}
            {m?.level ? ` · Lv.${m.level}` : ''}
          </div>
          <div className="inv-d-id">#{entry.itemId}</div>
        </div>
      </div>
      {children}
      {m?.description && (
        <div className="inv-d-sec">
          <span className="inv-d-label">說明</span>
          <div className="inv-d-desc">{m.description}</div>
        </div>
      )}
      {m && m.stats.length > 0 && (
        <div className="inv-d-sec">
          <span className="inv-d-label">屬性</span>
          <dl className="inv-stats">
            {m.stats.map(s => (
              <div key={s.label} style={{ display: 'contents' }}>
                <dt>{s.label}</dt>
                <dd>{s.value > 0 ? `+${s.value}` : s.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {tags.length > 0 && (
        <div className="inv-d-sec inv-tags">
          {tags.map(t => <span key={t.text} className={`inv-tag${t.tone ? ` is-${t.tone}` : ''}`}>{t.text}</span>)}
        </div>
      )}
    </aside>
  );
}

/** How many of an item one character has in each container. */
export function ContainerHoldings({ slots, itemId }: { slots: Item[]; itemId: number }) {
  const held = holdings(slots, itemId);
  return (
    <div className="inv-d-sec">
      <span className="inv-d-label">持有</span>
      <div className="inv-holds">
        {SOURCES.map(s => (
          <div key={s} className={held[s] ? 'inv-hold' : 'inv-hold is-zero'}>
            <span>{SOURCE_LABEL[s]}</span>
            <span>{held[s].toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
