import { useCallback, useEffect, useMemo, useState } from 'react';
import { del, get, post, put } from '../api/client';
import type {
  DispatchCandidate, DispatchPlan, DispatchRow, DispatchStatus, GuardStartResult, LoginEntry, LoginEntryIn,
} from '../api/types';
import { LoginDialog } from '../components/LoginDialog';
import { LoginTransferDialog } from '../components/LoginTransferDialog';
import { describeError, reportClientError } from '../diag/report';
import { Panel } from '../primitives';
import './dispatch.css';

const PLAN_MS = 5_000;
const STATUS_MS = 1_500;

const VERDICT: Record<DispatchCandidate['verdict'], { text: string; tone: string }> = {
  run: { text: '可以跑', tone: 'ok' },
  done: { text: '今日已完成', tone: 'mute' },
  blocked: { text: '設定不完整', tone: 'warn' },
  'no-login': { text: '不能登入', tone: 'warn' },
};

const ROW_STATE: Record<DispatchRow['state'], { text: string; tone: string }> = {
  pending: { text: '排隊中', tone: 'mute' },
  skipped: { text: '跳過', tone: 'mute' },
  login: { text: '登入中', tone: 'live' },
  running: { text: '跑日常', tone: 'live' },
  done: { text: '完成', tone: 'ok' },
  failed: { text: '失敗', tone: 'bad' },
  stopped: { text: '停下', tone: 'warn' },
};

function minutes(from?: number | null, to?: number | null): string {
  if (!from) return '';
  const secs = Math.max(0, Math.round((to ?? Date.now() / 1000) - from));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}

export function Dispatch() {
  const [plan, setPlan] = useState<DispatchPlan | null>(null);
  const [status, setStatus] = useState<DispatchStatus | null>(null);
  const [picked, setPicked] = useState<Set<string> | null>(null); // null: the default pick
  const [windows, setWindows] = useState<Set<number> | null>(null);
  const [editing, setEditing] = useState<LoginEntry | null>(null);
  const [transfer, setTransfer] = useState<'export' | 'import' | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const loadPlan = useCallback(() => {
    get<DispatchPlan>('/api/dispatch/plan')
      .then(setPlan)
      .catch(e => reportClientError(e, { component: 'Dispatch.plan', silent: true }));
  }, []);
  const loadStatus = useCallback(() => {
    get<DispatchStatus>('/api/dispatch')
      .then(setStatus)
      .catch(e => reportClientError(e, { component: 'Dispatch.status', silent: true }));
  }, []);

  useEffect(() => {
    loadPlan();
    loadStatus();
    const a = window.setInterval(loadPlan, PLAN_MS);
    const b = window.setInterval(loadStatus, STATUS_MS);
    return () => { window.clearInterval(a); window.clearInterval(b); };
  }, [loadPlan, loadStatus]);

  const running = status?.running ?? false;
  const candidates = plan?.candidates ?? [];
  const usableWindows = (plan?.windows ?? []).filter(w => !w.problem);
  // Until the user picks, tick what a run would actually do.
  const chosen = useMemo(
    () => picked ?? new Set(candidates.filter(c => c.verdict === 'run').map(c => c.entry.character)),
    [picked, candidates],
  );
  // A window in game with a character outside this run is someone being
  // played: not ticked unless the user ticks it (it would be logged out).
  const chosenWindows = useMemo(
    () => windows ?? new Set(usableWindows.filter(w => !w.name || chosen.has(w.name)).map(w => w.pid)),
    [windows, usableWindows, chosen],
  );

  const toggle = (name: string, on: boolean) => {
    const next = new Set(chosen);
    if (on) next.add(name); else next.delete(name);
    setPicked(next);
  };
  const toggleWindow = (pid: number, on: boolean) => {
    const next = new Set(chosenWindows);
    if (on) next.add(pid); else next.delete(pid);
    setWindows(next);
  };

  const start = async (dryRun: boolean) => {
    setBusy(true);
    setMessage(null);
    try {
      const characters = candidates.map(c => c.entry.character).filter(n => chosen.has(n));
      const r = await post<GuardStartResult>('/api/dispatch', {
        characters, pids: [...chosenWindows], dry_run: dryRun,
      });
      if (!r.ok) setMessage(r.reason ?? '派發開不起來');
      loadStatus();
    } catch (e) {
      setMessage(`派發開不起來：${describeError(e)}`);
      reportClientError(e, { component: 'Dispatch.start' });
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await post('/api/dispatch/stop');
      loadStatus();
    } catch (e) {
      reportClientError(e, { component: 'Dispatch.stop' });
    } finally {
      setBusy(false);
    }
  };

  const setEnabled = async (entry: LoginEntry, enabled: boolean) => {
    const body: LoginEntryIn = {
      character: entry.character, username: entry.username, server: entry.server,
      enabled, sort: entry.sort, password: null, protect: null,
    };
    try {
      await put('/api/logins', body);
      loadPlan();
    } catch (e) {
      setMessage(`更新失敗：${describeError(e)}`);
      reportClientError(e, { component: 'Dispatch.enable' });
    }
  };

  const remove = async (entry: LoginEntry) => {
    if (!window.confirm(`把 ${entry.character} 從自動登入移除？帳密會一起刪掉。`)) return;
    try {
      await del(`/api/logins/${encodeURIComponent(entry.character)}`);
      loadPlan();
    } catch (e) {
      setMessage(`移除失敗：${describeError(e)}`);
      reportClientError(e, { component: 'Dispatch.remove' });
    }
  };

  const canStart = !running && !busy && chosen.size > 0 && chosenWindows.size > 0;

  return (
    <div className="dp">
      <Panel title="帳號派發">
        <p className="dp-intro">
          勾選的角色會分給勾選的遊戲視窗：登入 → 跑「日常」清單 → 登出，再換下一隻，直到全部跑完。
          角色要先在遊戲裡登入一次，從角色名字旁的「加入自動登入」記下帳號。
        </p>

        <h3 className="dp-h">遊戲視窗</h3>
        {(plan?.windows ?? []).length === 0 ? (
          <p className="dp-empty">沒有開著的遊戲視窗。</p>
        ) : (
          <ul className="dp-windows">
            {(plan?.windows ?? []).map(w => {
              const id = `dp-w-${w.pid}`;
              return (
                <li key={w.pid} className={w.problem ? 'is-out' : undefined}>
                  <input
                    id={id} type="checkbox" disabled={!!w.problem || running}
                    checked={!w.problem && chosenWindows.has(w.pid)}
                    onChange={e => toggleWindow(w.pid, e.target.checked)}
                  />
                  <label htmlFor={id}>
                    <span className="dp-wname">{w.name ? `遊戲中：${w.name}` : '未登入'}</span>
                    <span className="dp-dim">pid {w.pid}</span>
                    {w.problem
                      ? <span className="dp-why">{w.problem}</span>
                      : w.name && (chosen.has(w.name)
                        ? <span className="dp-dim">先跑這隻，不用重新登入</span>
                        : <span className="dp-why">不在這次名單，勾了會被登出</span>)}
                  </label>
                </li>
              );
            })}
          </ul>
        )}

        <div className="dp-hrow">
          <h3 className="dp-h">角色</h3>
          <span className="dp-spacer" />
          <button type="button" className="dp-link" onClick={() => setTransfer('import')} disabled={running}>
            匯入帳號
          </button>
          <button
            type="button" className="dp-link" onClick={() => setTransfer('export')}
            disabled={running || candidates.length === 0}
            title="把帳號清單（含密碼）加密成檔案，帶到其他電腦匯入"
          >
            匯出帳號
          </button>
        </div>
        {candidates.length === 0 ? (
          <p className="dp-empty">還沒有角色。到角色頁面，按名字旁的「加入自動登入」。</p>
        ) : (
          <table className="dp-table">
            <thead>
              <tr>
                <th scope="col"><span className="dp-sr">選取</span></th>
                <th scope="col">角色</th>
                <th scope="col">帳號</th>
                <th scope="col">伺服器</th>
                <th scope="col">今日</th>
                <th scope="col">保護密碼</th>
                <th scope="col"><span className="dp-sr">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {candidates.map(c => {
                const e = c.entry;
                const v = VERDICT[c.verdict];
                const id = `dp-c-${e.character}`;
                return (
                  <tr key={e.character} data-off={!e.enabled || undefined}>
                    <td>
                      <input
                        id={id} type="checkbox" disabled={running}
                        checked={chosen.has(e.character)}
                        onChange={ev => toggle(e.character, ev.target.checked)}
                        aria-label={`派發 ${e.character}`}
                      />
                    </td>
                    <td><label htmlFor={id} className="dp-name">{e.character}</label></td>
                    <td className="dp-mono">{e.username}</td>
                    <td>{e.server}</td>
                    <td>
                      <span className={`dp-chip dp-${v.tone}`}>{v.text}</span>
                      {c.reason && c.verdict !== 'run' && <span className="dp-why">{c.reason}</span>}
                    </td>
                    <td className="dp-dim">{e.has_protect ? '有' : '—'}</td>
                    <td className="dp-ops">
                      <button type="button" onClick={() => setEditing(e)} disabled={running}>修改</button>
                      <button type="button" onClick={() => setEnabled(e, !e.enabled)} disabled={running}>
                        {e.enabled ? '停用' : '啟用'}
                      </button>
                      <button type="button" className="dp-del" onClick={() => remove(e)} disabled={running}>移除</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <div className="dp-actions">
          {running ? (
            <button type="button" className="dp-btn dp-stop" onClick={stop} disabled={busy}>停止派發</button>
          ) : (
            <>
              <button type="button" className="dp-btn dp-primary" onClick={() => start(false)} disabled={!canStart}>
                開始派發（{chosen.size} 隻 · {chosenWindows.size} 個視窗）
              </button>
              <button
                type="button" className="dp-btn" onClick={() => start(true)} disabled={!canStart}
                title="每隻只登入、確認角色、登出，不跑日常：用來檢查帳密設定，不會用掉每日次數"
              >
                試跑（只檢查登入）
              </button>
            </>
          )}
          {message && <span className="dp-msg" role="alert">{message}</span>}
        </div>
        <p className="dp-dim dp-foot">
          停止後不再派新的角色，正在跑的角色停在原地，不會被登出。同一個帳號同時只會在一個視窗登入。
        </p>
      </Panel>

      {status && status.rows.length > 0 && <Progress status={status} />}

      {transfer && (
        <LoginTransferDialog mode={transfer} onClose={() => setTransfer(null)} onDone={loadPlan} />
      )}
      {editing && (
        <LoginDialog
          pid={null} name={editing.character} entry={editing}
          onClose={() => setEditing(null)} onSaved={() => loadPlan()}
        />
      )}
    </div>
  );
}

function Progress({ status }: { status: DispatchStatus }) {
  const done = status.rows.filter(r => r.state === 'done').length;
  const left = status.rows.filter(r => r.state === 'pending' || r.state === 'login' || r.state === 'running').length;
  return (
    <Panel title={status.running ? (status.dry_run ? '試跑中' : '派發中') : (status.dry_run ? '上次試跑' : '上次派發')}>
      <p className="dp-summary">
        完成 {done} · 剩 {left} · 共 {status.rows.length}
        {status.started_at && <span className="dp-dim"> · 開始於 {new Date(status.started_at * 1000).toLocaleTimeString('zh-TW', { hour12: false })}</span>}
      </p>
      <ul className="dp-lanes">
        {status.windows.map(w => (
          <li key={w.pid}>
            <span className="dp-dim">pid {w.pid}</span>
            {w.character
              ? <span><b>{w.character}</b>　{w.step ?? ''}</span>
              : <span className="dp-dim">{w.problem ?? (status.running ? '等待中' : '結束')}</span>}
            <span className="dp-dim">完成 {w.done}</span>
          </li>
        ))}
      </ul>
      <table className="dp-table">
        <thead>
          <tr>
            <th scope="col">角色</th>
            <th scope="col">狀態</th>
            <th scope="col">視窗</th>
            <th scope="col">時間</th>
            <th scope="col">說明</th>
          </tr>
        </thead>
        <tbody>
          {status.rows.map(r => {
            const s = ROW_STATE[r.state];
            return (
              <tr key={r.character}>
                <td className="dp-name">{r.character}</td>
                <td><span className={`dp-chip dp-${s.tone}`}>{s.text}</span></td>
                <td className="dp-dim">{r.pid ?? '—'}</td>
                <td className="dp-mono">{minutes(r.started_at, r.ended_at)}</td>
                <td className="dp-reason">{r.reason ?? ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}
