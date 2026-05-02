import type { CSSProperties } from 'react';

export function FrameCorners({ size = 10 }: { size?: number }) {
  const c: CSSProperties = {
    position: 'absolute', width: size, height: size, borderColor: 'var(--tt-accent)',
    borderStyle: 'solid', borderWidth: 0,
  };
  return (
    <>
      <span style={{ ...c, top: 0, left: 0, borderTopWidth: 1, borderLeftWidth: 1 }} />
      <span style={{ ...c, top: 0, right: 0, borderTopWidth: 1, borderRightWidth: 1 }} />
      <span style={{ ...c, bottom: 0, left: 0, borderBottomWidth: 1, borderLeftWidth: 1 }} />
      <span style={{ ...c, bottom: 0, right: 0, borderBottomWidth: 1, borderRightWidth: 1 }} />
    </>
  );
}
