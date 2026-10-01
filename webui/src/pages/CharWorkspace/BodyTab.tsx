import { useState } from 'react';
import type { CharacterDetail, EquipSlot, ItemMeta } from '../../api/types';
import { ItemIcon } from '../../components/items/ItemCells';
import { ItemDetail } from '../../components/items/ItemDetail';
import type { Entry } from '../../components/items/entries';
import { useItemMeta } from '../../components/items/useItemMeta';
import { Panel, StatNum } from '../../primitives';
import '../../components/items/items.css';

const SLOT_LABEL: Record<EquipSlot['slot'], string> = {
  CAP: '帽', BODY: '衣', FOOT: '鞋', WING: '背飾', HORSE: '座騎',
  ORNAMENT_1: '左飾', ORNAMENT_2: '中飾', ORNAMENT_3: '右飾', HAND_L: '左手', HAND_R: '右手',
};

export function BodyTab({ detail, error }: { detail: CharacterDetail | null; error: string | null }) {
  const gear = detail?.equipment ?? [];
  const meta = useItemMeta(gear.flatMap(g => (g.item_id ? [g.item_id] : [])));
  const [selected, setSelected] = useState<EquipSlot['slot'] | null>(null);

  if (!detail) {
    return error
      ? <div role="status" style={{ color: 'var(--tt-bad)', fontSize: 13 }}>讀取失敗：{error}</div>
      : <div style={{ color: 'var(--tt-dim)' }}>讀取中…</div>;
  }
  const s = detail.stats;
  const six = [
    ['外功', s.waigong], ['內力', s.neili], ['根骨', s.genggu],
    ['身法', s.shenfa], ['技巧', s.jiqiao], ['玄學', s.xuanxue],
  ] as const;
  const seven = [
    ['物攻', s.wugong], ['基礎', s.wugong_base], ['內勁', s.neijing],
    ['防禦', s.fangyu], ['護勁', s.huji], ['命中', s.mingzhong], ['閃躲', s.shanduo],
  ] as const;

  const worn = gear.filter(g => g.item_id);
  const current = worn.find(g => g.slot === selected) ?? worn[0];
  const bonus = sumStats(worn.map(g => meta.get(g.item_id!)));

  // Buffs live in the workspace header, so this tab shows stats and worn gear.
  return (
    <div className="inv-main">
      <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
        <Panel title="披掛">
          {detail.equipment == null
            ? <div className="inv-empty">等待角色定位後自動讀取</div>
            : (
              <div className="body-gear">
                {gear.map(g => (
                  <GearRow
                    key={g.slot} slot={g} meta={g.item_id ? meta.get(g.item_id) : undefined}
                    selected={current?.slot === g.slot} onSelect={() => setSelected(g.slot)}
                  />
                ))}
              </div>
            )}
        </Panel>
        <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
          <Panel title="六屬"><Grid pairs={six} /></Panel>
          <Panel title="七戰"><Grid pairs={seven} /></Panel>
          {bonus.length > 0 && (
            <Panel title={<>裝備加成 <span className="body-note">不含強化</span></>}>
              <Grid pairs={bonus} signed />
            </Panel>
          )}
        </div>
      </div>
      <ItemDetail entry={current ? toEntry(current, meta.get(current.item_id!)) : undefined} />
    </div>
  );
}

function GearRow({ slot, meta, selected, onSelect }: {
  slot: EquipSlot; meta: ItemMeta | undefined; selected: boolean; onSelect: () => void;
}) {
  const label = SLOT_LABEL[slot.slot];
  if (!slot.item_id) {
    return (
      <div className="body-gear-row is-empty">
        <span className="body-gear-slot">{label}</span>
        <span className="inv-row-icon" aria-hidden="true" />
        <span className="inv-row-sub">未裝備</span>
      </div>
    );
  }
  const name = meta?.name || slot.name || `#${slot.item_id}`;
  const plus = slot.plus > 0 ? `+${slot.plus}` : '';
  const sub = [meta?.type_label, meta?.level ? `Lv.${meta.level}` : ''].filter(Boolean).join(' · ');
  return (
    <button
      type="button" className="body-gear-row" aria-pressed={selected} onClick={onSelect}
      aria-label={`${label}：${name}${plus}`}
    >
      <span className="body-gear-slot">{label}</span>
      <span className="inv-row-icon"><ItemIcon name={name} meta={meta} size={36} /></span>
      <span className="inv-row-name">
        <span>{name}{plus && <span className="body-plus">{plus}</span>}</span>
        {sub && <span className="inv-row-sub">{sub}</span>}
      </span>
    </button>
  );
}

function toEntry(slot: EquipSlot, meta: ItemMeta | undefined): Entry {
  return {
    key: slot.slot,
    itemId: slot.item_id!,
    // The tooltip shows "name(+N)"; keep the same shape in the detail header.
    name: `${meta?.name || slot.name || `#${slot.item_id}`}${slot.plus > 0 ? ` +${slot.plus}` : ''}`,
    qty: 1,
    stacks: 1,
    sources: [],
    meta,
    category: meta?.category ?? 'gear',
  };
}

/** Base item stats summed over the worn gear, in first-seen label order. */
function sumStats(metas: (ItemMeta | undefined)[]): [string, number][] {
  const total = new Map<string, number>();
  for (const m of metas) {
    for (const st of m?.stats ?? []) total.set(st.label, (total.get(st.label) ?? 0) + st.value);
  }
  return [...total].filter(([, v]) => v !== 0);
}

function Grid({ pairs, signed }: { pairs: readonly (readonly [string, number])[]; signed?: boolean }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
      {pairs.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <span style={{ color: 'var(--tt-dim)', letterSpacing: 2 }}>{k}</span>
          {signed && v > 0
            ? <span className="body-signed">+<StatNum value={v} /></span>
            : <StatNum value={v} />}
        </div>
      ))}
    </div>
  );
}
