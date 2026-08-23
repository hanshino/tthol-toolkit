import { Component, type ErrorInfo, type ReactNode } from 'react';
import { reportClientError } from '../diag/report';

type Props = { component: string; children: ReactNode };
type State = { message: string | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { message: null };

  static getDerivedStateFromError(err: unknown): State {
    return { message: err instanceof Error ? err.message : String(err) };
  }

  componentDidCatch(err: Error, info: ErrorInfo): void {
    reportClientError(err, { component: `${this.props.component}${info.componentStack ?? ''}` });
  }

  render(): ReactNode {
    if (this.state.message === null) return this.props.children;
    return (
      <div style={{ padding: 24, fontFamily: 'var(--tt-font)' }}>
        <div style={{
          fontFamily: 'var(--tt-font-serif)', fontSize: 16, letterSpacing: 4,
          color: 'var(--tt-bad)', marginBottom: 10,
        }}>
          此頁出錯
        </div>
        <div style={{ color: 'var(--tt-text)', fontSize: 13, marginBottom: 14 }}>
          {this.state.message}
        </div>
        <div style={{ color: 'var(--tt-dim)', fontSize: 12, marginBottom: 14 }}>
          錯誤已記錄，可到「脈案」分頁匯出診斷包。
        </div>
        <button onClick={() => this.setState({ message: null })}>重試</button>
      </div>
    );
  }
}
