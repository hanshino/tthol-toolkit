import { useEffect, useState } from 'react';
import type { BuffInfo } from '../api/types';

/**
 * Row of active-status chips. With a hook (source "hook") a chip names the
 * exact skill or item, its level, and counts down to `expires_at`; without one
 * (source "memory") only the status group's name is known. Gold = buff,
 * red = debuff, accent = hero transform (shown as 變身中, not which hero).
 * The text carries the meaning, not colour alone; `title` adds the detail.
 */
export function BuffChips({ buffs, emptyText }: { buffs?: BuffInfo[]; emptyText?: string }) {
  const timed = !!buffs?.some(b => b.expires_at != null);
  const now = useNow(timed);

  if (!buffs || buffs.length === 0) {
    return emptyText
      ? <span style={{ color: 'var(--tt-mute)', fontSize: 11 }}>{emptyText}</span>
      : null;
  }
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {buffs.map((b, i) => {
        const accent =
          b.kind === 'debuff' ? 'var(--tt-bad)' : b.kind === 'hero' ? 'var(--tt-accent)' : 'var(--tt-gold)';
        const label = b.kind === 'hero' ? '變身中' : b.name;
        const left = b.expires_at != null ? Math.max(0, Math.round(b.expires_at - now)) : null;
        const kindText = b.kind === 'debuff' ? '負面' : b.kind === 'hero' ? '英雄變身' : '增益';
        const title = [
          b.kind === 'hero' ? `英雄變身（${b.name}）` : b.name,
          b.level != null ? `Lv${b.level}` : null,
          kindText,
          left != null ? `剩 ${fmtLeft(left)}` : b.source === 'hook' ? '剩餘時間不明' : null,
          b.source === 'memory' ? '只知道狀態類別' : null,
        ].filter(Boolean).join('・');
        return (
          <span
            key={`${b.code ?? `g${b.group}`}-${i}`}
            title={title}
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
            {label}
            {b.level != null && <span style={{ fontSize: 10, opacity: 0.8, letterSpacing: 0 }}>Lv{b.level}</span>}
            {left != null && (
              <span style={{ fontFamily: 'var(--tt-font-mono)', fontSize: 10, color: 'var(--tt-dim)', letterSpacing: 0 }}>
                {fmtLeft(left)}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}

function fmtLeft(s: number): string {
  if (s >= 3600) return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Wall-clock seconds, ticking once a second only while a countdown is shown.
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now() / 1000);
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}
