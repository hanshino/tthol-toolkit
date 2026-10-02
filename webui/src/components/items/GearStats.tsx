import type { Inlay, ItemMeta, ItemStat } from '../../api/types';
import { ItemIcon } from './ItemCells';

/** The parts of a gear instance the tooltip shows: worn gear and stall listings both have them. */
export type GearView = {
  plus: number;
  stats: ItemStat[];
  enhance: ItemStat[];
  enhance_extra?: ItemStat[];
};

/**
 * Stats as the game tooltip shows them: own value (with 真元), then "+x" from
 * the enhancement level and "(+x)" from unlocked enhancement milestones.
 */
export function GearStats({ gear }: { gear: GearView }) {
  const enh = new Map(gear.enhance.map(s => [s.label, s.value]));
  const extra = new Map((gear.enhance_extra ?? []).map(s => [s.label, s.value]));
  const labels = [...new Set([
    ...gear.stats.map(s => s.label), ...enh.keys(), ...extra.keys(),
  ])];
  const own = new Map(gear.stats.map(s => [s.label, s.value]));
  if (labels.length === 0) return null;
  const enhanced = enh.size > 0 || extra.size > 0;
  return (
    <div className="inv-d-sec">
      <span className="inv-d-label">
        屬性{enhanced && <span className="body-note">金色為 +{gear.plus} 強化加成</span>}
      </span>
      <dl className="inv-stats">
        {labels.map(label => {
          const v = own.get(label) ?? 0;
          const p = enh.get(label) ?? 0;
          const x = extra.get(label) ?? 0;
          return (
            <div key={label} style={{ display: 'contents' }}>
              <dt>{label}</dt>
              <dd>
                {v !== 0 && (v > 0 ? `+${v}` : v)}
                {p !== 0 && <span className="body-plus">+{p}</span>}
                {x !== 0 && <span className="body-plus">(+{x})</span>}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

/** 真元 / 魂石 set into the item, one row per kind. */
export function GearInlays({ inlays, meta }: { inlays: Inlay[]; meta: Map<number, ItemMeta> }) {
  if (inlays.length === 0) return null;
  return (
    <div className="inv-d-sec">
      <span className="inv-d-label">鑲嵌</span>
      <div className="body-inlays">
        {inlays.map(i => (
          <div key={i.item_id} className="body-inlay">
            <span className="inv-row-icon"><ItemIcon name={i.name} meta={meta.get(i.item_id)} size={28} /></span>
            <span className="inv-row-name">
              <span>{i.name}{i.count > 1 && <span className="body-count">×{i.count}</span>}</span>
              <span className="inv-row-sub">{i.effect}{i.count > 1 ? ' / 顆' : ''}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
