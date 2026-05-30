import type { ReactNode } from 'react';

export function Seal({ size = 36, children }: { size?: number; children: ReactNode }) {
  return (
    <span
      style={{
        width: size, height: size, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--tt-seal)', color: '#fff', fontFamily: 'var(--tt-font-serif)',
        fontWeight: 600, fontSize: size * 0.5, letterSpacing: 0,
      }}
    >
      {children}
    </span>
  );
}
