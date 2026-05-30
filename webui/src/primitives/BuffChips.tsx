import type { BuffInfo } from '../api/types';

/**
 * Row of active-status chips. The game stores a status `group`, so each chip
 * shows the representative group name (e.g. 護體 / 血契 / 中毒). A gold pill
 * marks a positive buff, a red pill a debuff — but the name (not colour alone)
 * carries the meaning, and `title` exposes the group id + kind for
 * accessibility. Driven by whatever the backend reports, so any status array
 * added in knowledge.json shows up here automatically.
 */
export function BuffChips({ buffs, emptyText }: { buffs?: BuffInfo[]; emptyText?: string }) {
  if (!buffs || buffs.length === 0) {
    return emptyText
      ? <span style={{ color: 'var(--tt-mute)', fontSize: 11 }}>{emptyText}</span>
      : null;
  }
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {buffs.map((b, i) => {
        const isDebuff = b.kind === 'debuff';
        const accent = isDebuff ? 'var(--tt-bad)' : 'var(--tt-gold)';
        return (
          <span
            key={`${b.group}-${i}`}
            title={`${b.name}（group ${b.group}・${isDebuff ? '負面' : '增益'}）`}
            style={{
              fontSize: 11,
              lineHeight: 1.5,
              padding: '1px 8px',
              background: 'var(--tt-raised)',
              border: `1px solid ${accent}`,
              color: accent,
              borderRadius: 10,
              whiteSpace: 'nowrap',
              fontFamily: 'var(--tt-font-serif)',
              letterSpacing: 1,
            }}
          >
            {b.name}
          </span>
        );
      })}
    </div>
  );
}
