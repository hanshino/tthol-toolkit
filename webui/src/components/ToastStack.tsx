export interface Toast { id: string; tone: 'ok' | 'warn' | 'bad'; text: string; }

export function ToastStack({ toasts }: { toasts: Toast[] }) {
  return (
    <div style={{ position: 'fixed', right: 16, bottom: 16, display: 'grid', gap: 8, zIndex: 100 }}>
      {toasts.map(t => (
        <div key={t.id} style={{
          background: 'var(--tt-panel)', border: `1px solid var(--tt-${t.tone})`,
          padding: '8px 12px', fontSize: 12, color: 'var(--tt-text)',
        }}>{t.text}</div>
      ))}
    </div>
  );
}
