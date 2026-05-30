export type LinkStatus = 'ok' | 'weak' | 'lost';

const STATUS_COLOR: Record<LinkStatus, string> = {
  ok: 'var(--tt-ok)',
  weak: 'var(--tt-warn)',
  lost: 'var(--tt-bad)',
};

export function LinkDot({ status, size = 8 }: { status: LinkStatus; size?: number }) {
  return (
    <span
      style={{
        display: 'inline-block', width: size, height: size, borderRadius: '50%',
        background: STATUS_COLOR[status],
        boxShadow: status === 'ok' ? `0 0 ${size}px ${STATUS_COLOR[status]}` : undefined,
      }}
    />
  );
}
