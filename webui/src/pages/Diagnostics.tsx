import { useCallback, useEffect, useRef, useState } from 'react';
import { get, put } from '../api/client';
import { describeError, reportClientError } from '../diag/report';
import { Panel } from '../primitives';
import type { DiagEventModel, DiagSummary, VerboseState } from '../api/types';

const POLL_MS = 2_000;
const PAGE_SIZE = 200;

// Level is conveyed by a text marker as well as colour: colour alone fails
// users who cannot distinguish it.
const MARK: Record<string, { text: string; color: string }> = {
  ERROR:   { text: '錯', color: 'var(--tt-bad)' },
  WARNING: { text: '警', color: 'var(--tt-warn)' },
  INFO:    { text: '訊', color: 'var(--tt-dim)' },
  DEBUG:   { text: '詳', color: 'var(--tt-dim)' },
};

function clockOf(v: number): string {
  return new Date(v * 1000).toLocaleTimeString('zh-TW', { hour12: false });
}

export function Diagnostics() {
  const [summary, setSummary] = useState<DiagSummary | null>(null);
  const [events, setEvents] = useState<DiagEventModel[]>([]);
  const [level, setLevel] = useState('');
  const [pid, setPid] = useState('');
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(PAGE_SIZE);
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lastTs = useRef(0);

  const loadSummary = useCallback(() => {
    get<DiagSummary>('/api/diagnostics/summary')
      .then(setSummary)
      .catch(e => reportClientError(e, { component: 'Diagnostics.summary', silent: true }));
  }, []);

  useEffect(() => {
    let alive = true;
    loadSummary();

    const pull = () => {
      const since = lastTs.current ? `?since=${lastTs.current}` : '';
      get<DiagEventModel[]>(`/api/diagnostics/events${since}`)
        .then(fresh => {
          if (!alive || fresh.length === 0) return;
          lastTs.current = Math.max(lastTs.current, ...fresh.map(e => e.ts));
          setEvents(prev => [...fresh, ...prev]);
        })
        .catch(e => reportClientError(e, { component: 'Diagnostics.events', silent: true }));
    };
    pull();
    const timer = window.setInterval(pull, POLL_MS);
    return () => { alive = false; window.clearInterval(timer); };
  }, [loadSummary]);

  const toggleVerbose = async () => {
    if (!summary) return;
    setBusy(true);
    try {
      const next = await put<VerboseState>('/api/diagnostics/verbose', {
        verbose: !summary.verbose,
      });
      setSummary({ ...summary, verbose: next.verbose });
      setToast(next.verbose ? '詳細記錄已開啟，請重現問題後再匯出' : '詳細記錄已關閉');
    } catch (e) {
      setToast(`切換失敗：${describeError(e)}`);
      reportClientError(e, { component: 'Diagnostics.verbose' });
    } finally {
      setBusy(false);
    }
  };

  const filtered = events.filter(e =>
    (!level || e.level === level)
    && (!pid || String(e.pid ?? '') === pid)
    && (!query || e.message.includes(query) || (e.code ?? '').includes(query)),
  );
  const visible = filtered.slice(0, shown);
  const pids = Array.from(new Set(events.map(e => e.pid).filter((p): p is number => p != null)));
  const env = (summary?.environment ?? {}) as Record<string, unknown>;

  return (
    <div style={{ padding: 18, display: 'grid', gap: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 14 }}>
        <Panel title="環境">
          {summary ? (
            <dl style={{
              margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr',
              gap: '4px 14px', fontSize: 12, fontFamily: 'var(--tt-font-mono)',
            }}>
              <Row label="版本" value={String(env.app_version ?? '—')} />
              <Row
                label="紀錄檔"
                value={summary.events_path ?? '(記憶體暫存，未落地)'}
                wrap
              />
              <Row label="玩家鏈" value={String(env.player_hp_chain_base ?? '—')} />
              <Row label="知識庫" value={String(env.knowledge_sha8 ?? '—')} />
              <Row
                label="連線角色"
                value={
                  summary.sessions.length === 0
                    ? '(無)'
                    : summary.sessions
                        .map(s => `${(s as Record<string, unknown>).name ?? (s as Record<string, unknown>).pid} (${(s as Record<string, unknown>).link})`)
                        .join('、')
                }
                wrap
              />
            </dl>
          ) : (
            <div style={{ color: 'var(--tt-dim)', fontSize: 12 }}>載入中…</div>
          )}
        </Panel>

        <Panel title="操作">
          <div style={{ display: 'grid', gap: 10 }}>
            <button
              onClick={toggleVerbose}
              disabled={busy || !summary}
              className={summary?.verbose ? 'is-active' : undefined}
            >
              詳細記錄：{summary?.verbose ? '開' : '關'}
            </button>
            <a
              href="/api/diagnostics/bundle"
              download
              style={{
                display: 'block', textAlign: 'center', padding: '6px 14px',
                border: '1px solid var(--tt-line)', color: 'var(--tt-text)',
                textDecoration: 'none', fontSize: 13, letterSpacing: 2,
                cursor: 'pointer',
              }}
            >
              匯出診斷包
            </a>
            <div style={{ fontSize: 11, color: 'var(--tt-dim)', lineHeight: 1.6 }}>
              包含：錯誤紀錄、角色名稱與座標、道具清單、程式版本與安裝路徑。
              僅在你主動匯出時產生，程式不會自行上傳。
            </div>
          </div>
        </Panel>
      </div>

      <Panel title={`事件（${filtered.length}）`}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
          <select value={level} onChange={e => setLevel(e.target.value)} aria-label="等級篩選">
            <option value="">全部等級</option>
            <option value="ERROR">錯誤</option>
            <option value="WARNING">警告</option>
            <option value="INFO">訊息</option>
            <option value="DEBUG">詳細</option>
          </select>
          <select value={pid} onChange={e => setPid(e.target.value)} aria-label="角色篩選">
            <option value="">全部角色</option>
            {pids.map(p => <option key={p} value={String(p)}>pid {p}</option>)}
          </select>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜尋訊息或錯誤碼"
            aria-label="搜尋訊息或錯誤碼"
            style={{
              flex: 1, minWidth: 160, background: 'var(--tt-bg)',
              border: '1px solid var(--tt-line)', color: 'var(--tt-text)',
              padding: '6px 10px', fontSize: 12,
            }}
          />
        </div>

        <div style={{ maxHeight: 380, overflowY: 'auto', overflowX: 'auto' }}>
          {visible.length === 0 ? (
            <div style={{ color: 'var(--tt-dim)', fontSize: 12, padding: 8 }}>目前沒有事件。</div>
          ) : visible.map((e, i) => {
            const mark = MARK[e.level] ?? MARK.INFO;
            return (
              <div key={`${e.ts}-${i}`} style={{
                display: 'grid', gridTemplateColumns: '24px 72px 1fr',
                gap: 10, padding: '5px 4px', fontSize: 12,
                borderBottom: '1px solid var(--tt-line-soft)',
                fontFamily: 'var(--tt-font-mono)',
              }}>
                <span
                  title={e.level}
                  style={{ color: mark.color, fontFamily: 'var(--tt-font-serif)' }}
                >
                  {mark.text}
                </span>
                <span style={{ color: 'var(--tt-dim)' }}>{clockOf(e.ts)}</span>
                <span style={{ color: 'var(--tt-text)', wordBreak: 'break-word' }}>
                  {e.code && <code style={{ color: mark.color, marginRight: 8 }}>{e.code}</code>}
                  {e.char && <span style={{ color: 'var(--tt-dim)' }}>[{e.char}] </span>}
                  {e.message}
                  {e.detail && (
                    <details style={{ marginTop: 4 }}>
                      <summary style={{ color: 'var(--tt-dim)', cursor: 'pointer' }}>詳情</summary>
                      <pre style={{
                        margin: '4px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                        color: 'var(--tt-dim)', fontSize: 11,
                      }}>{JSON.stringify(e.detail, null, 2)}</pre>
                    </details>
                  )}
                </span>
              </div>
            );
          })}
        </div>

        {filtered.length > shown && (
          <button style={{ marginTop: 10 }} onClick={() => setShown(s => s + PAGE_SIZE)}>
            載入更多（尚有 {filtered.length - shown} 筆）
          </button>
        )}
      </Panel>

      {toast && (
        <div role="status" style={{
          position: 'fixed', bottom: 18, right: 18, padding: '10px 16px',
          background: 'var(--tt-raised)', border: '1px solid var(--tt-gold)',
          color: 'var(--tt-text)', fontSize: 12,
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}

function Row({ label, value, wrap }: { label: string; value: string; wrap?: boolean }) {
  return (
    <>
      <dt style={{ color: 'var(--tt-dim)' }}>{label}</dt>
      <dd style={{
        margin: 0, color: 'var(--tt-text)',
        wordBreak: wrap ? 'break-all' : 'normal',
      }}>{value}</dd>
    </>
  );
}
