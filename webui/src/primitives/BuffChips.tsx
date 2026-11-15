import type { BuffInfo } from '../api/types';

/**
 * Row of active-status chips, from the character's status arrays in memory:
 * only the status group's name is known. Gold = buff, red = debuff. The text
 * carries the meaning, not colour alone; `title` adds the detail.
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
        const accent = b.kind === 'debuff' ? 'var(--tt-bad)' : 'var(--tt-gold)';
        return (
          <span
            key={`g${b.group}-${i}`}
            title={`${b.name}・${b.kind === 'debuff' ? '負面' : '增益'}・只知道狀態類別`}
            style={{
              display: 'inline-flex',
              alignItems: 'baseline',
              gap: 5,
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
