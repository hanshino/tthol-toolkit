import { Bar, LinkDot, Panel, Seal, StatNum } from './primitives';
import { ThemeProvider } from './theme/ThemeProvider';

export function App() {
  return (
    <ThemeProvider>
      <div style={{ padding: 24, background: 'var(--tt-bg)', minHeight: '100vh', color: 'var(--tt-text)' }}>
        <h1 style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 4 }}>
          <Seal>御</Seal> 御心鑒
        </h1>
        <Panel title="primitives demo" style={{ maxWidth: 360 }}>
          <div style={{ display: 'grid', gap: 8 }}>
            <div><LinkDot status="ok" /> 已連</div>
            <div><LinkDot status="weak" /> 校驗中</div>
            <div><LinkDot status="lost" /> 斷線</div>
            <Bar value={120} max={150} tone="hp" />
            <StatNum value={120} max={150} />
          </div>
        </Panel>
      </div>
    </ThemeProvider>
  );
}
