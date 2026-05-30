export function StatNum({ value, max, dim }: { value: number; max?: number; dim?: boolean }) {
  return (
    <span style={{
      fontFamily: 'var(--tt-font-mono)',
      color: dim ? 'var(--tt-mute)' : 'var(--tt-text)',
      fontVariantNumeric: 'tabular-nums',
    }}>
      {value}{max !== undefined && <span style={{ color: 'var(--tt-mute)' }}>/{max}</span>}
    </span>
  );
}
