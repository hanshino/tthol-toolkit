import type { ChipMode } from '../theme/ThemeProvider';

export function CharChip({
  mode, name, idx, size = 24,
}: { mode: ChipMode; name: string; idx: number; size?: number }) {
  if (mode === 'avatar') {
    return (
      <span style={{
        width: size, height: size, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--tt-raised)', border: '1px solid var(--tt-line)',
        fontFamily: 'var(--tt-font-serif)', fontWeight: 600,
      }}>{name[0]}</span>
    );
  }
  if (mode === 'number') {
    return (
      <span style={{
        width: size, height: size, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--tt-raised)', border: '1px solid var(--tt-line)',
        fontFamily: 'var(--tt-font-mono)', fontSize: 11,
      }}>{String(idx + 1).padStart(2, '0')}</span>
    );
  }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', height: size,
      color: 'var(--tt-dim)', fontSize: 12,
    }}>{name}</span>
  );
}
