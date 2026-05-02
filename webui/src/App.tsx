import { ThemeProvider } from './theme/ThemeProvider';

export function App() {
  return (
    <ThemeProvider>
      <div style={{ padding: 24, color: 'var(--tt-text)', background: 'var(--tt-bg)', minHeight: '100vh', fontFamily: 'var(--tt-font)' }}>
        <h1 style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 4 }}>御心鑒</h1>
        <p>Theme tokens loaded.</p>
      </div>
    </ThemeProvider>
  );
}
