type Tone = 'hp' | 'mp' | 'weight' | 'plain';

const TONE_VARS: Record<Tone, string> = {
  hp: 'var(--tt-bad)',
  mp: 'var(--tt-accent)',
  weight: 'var(--tt-gold)',
  plain: 'var(--tt-dim)',
};

export function Bar({
  value, max, tone = 'plain', height = 6,
}: { value: number; max: number; tone?: Tone; height?: number }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div style={{ background: 'var(--tt-line-soft)', height, borderRadius: 1, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', background: TONE_VARS[tone], transition: 'width .25s' }} />
    </div>
  );
}
