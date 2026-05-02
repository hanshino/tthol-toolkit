import type { CSSProperties, ReactNode } from 'react';

export function Panel({
  title, children, style, id,
}: { title?: ReactNode; children: ReactNode; style?: CSSProperties; id?: string }) {
  return (
    <section id={id} style={{
      padding: 14, ...style,
      background: 'var(--tt-panel)', border: '1px solid var(--tt-line)',
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
