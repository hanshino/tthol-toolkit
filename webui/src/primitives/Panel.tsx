import type { CSSProperties, ReactNode } from 'react';

export function Panel({
  title, children, style,
}: { title?: ReactNode; children: ReactNode; style?: CSSProperties }) {
  return (
    <section style={{
      background: 'var(--tt-panel)', border: '1px solid var(--tt-line)', padding: 14, ...style,
    }}>
      {title && (
        <header style={{
          fontFamily: 'var(--tt-font-serif)', fontSize: 13, letterSpacing: 4, fontWeight: 600,
          color: 'var(--tt-dim)', marginBottom: 10,
        }}>
          {title}
        </header>
      )}
      {children}
    </section>
  );
}
