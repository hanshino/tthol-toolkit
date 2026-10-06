import { useEffect, useId, useRef, useState } from 'react';
import { del, get, put } from '../api/client';
import type { DispatchPlan, LoginEntry, LoginEntryIn, LoginForm } from '../api/types';
import { describeError, reportClientError } from '../diag/report';
import './login.css';

// 加入自動登入 for the character located in this window (pid set): the name
// comes from the game, the user fills in the account. From the 帳號派發 page
// (pid null) it edits a listed character. Secrets go to the backend on save
// and never come back: a stored one shows as 已設定, left empty = keep it.
export function LoginDialog({ pid, name, entry, onClose, onSaved }: {
  pid: number | null;
  name: string;
  entry: LoginEntry | null;
  onClose: () => void;
  onSaved: (entry: LoginEntry | null) => void;
}) {
  const titleId = useId();
  const [servers, setServers] = useState<string[]>([]);
  const [server, setServer] = useState(entry?.server ?? '');
  const [username, setUsername] = useState(entry?.username ?? '');
  const [password, setPassword] = useState('');
  const [protect, setProtect] = useState('');
  const [clearProtect, setClearProtect] = useState(false);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    first.current?.focus();
    get<DispatchPlan>('/api/dispatch/plan')
      .then(p => {
        setServers(p.servers);
        setServer(s => s || p.servers[0] || '');
      })
      .catch(e => reportClientError(e, { component: 'LoginDialog.servers', silent: true }));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const needPassword = !entry?.has_password;
  const canSave = !busy && username.trim() !== '' && server !== '' && (!needPassword || password !== '');

  const save = async () => {
    if (!canSave) return;
    setBusy(true);
    setError(null);
    const body: LoginForm = {
      username: username.trim(),
      server,
      enabled: entry?.enabled ?? true,
      password: password === '' ? null : password,
      protect: clearProtect ? '' : protect === '' ? null : protect,
    };
    try {
      if (pid !== null) {
        onSaved(await put<LoginEntry>(`/api/characters/${pid}/login`, body));
      } else {
        const edit: LoginEntryIn = { ...body, character: name, sort: entry?.sort ?? 0 };
        onSaved(await put<LoginEntry>('/api/logins', edit));
      }
      onClose();
    } catch (e) {
      setError(`儲存失敗：${describeError(e)}`);
      reportClientError(e, { component: 'LoginDialog.save' });
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await del(`/api/logins/${encodeURIComponent(name)}`);
      onSaved(null);
      onClose();
    } catch (e) {
      setError(`移除失敗：${describeError(e)}`);
      reportClientError(e, { component: 'LoginDialog.remove' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lg-backdrop" onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="lg-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="lg-head">
          <h2 id={titleId}>{entry ? '自動登入設定' : '加入自動登入'}</h2>
          <span className="lg-char">{name}</span>
        </header>
        <form className="lg-form" onSubmit={e => { e.preventDefault(); void save(); }}>
          <label className="lg-field">
            <span>伺服器</span>
            <select ref={first} value={server} onChange={e => setServer(e.target.value)} disabled={busy}>
              {!servers.includes(server) && server && <option value={server}>{server}</option>}
              {servers.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="lg-field">
            <span>帳號</span>
            <input
              value={username} onChange={e => setUsername(e.target.value)} disabled={busy}
              autoComplete="off" spellCheck={false} maxLength={64}
            />
          </label>
          <label className="lg-field">
            <span>密碼</span>
            <input
              type={show ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)}
              disabled={busy} autoComplete="new-password" maxLength={64}
              placeholder={entry?.has_password ? '已設定，不改請留空' : '必填'}
            />
          </label>
          <label className="lg-field">
            <span>保護密碼</span>
            <input
              type={show ? 'text' : 'password'} value={protect} onChange={e => setProtect(e.target.value)}
              disabled={busy || clearProtect} autoComplete="new-password" maxLength={64}
              placeholder={entry?.has_protect ? '已設定，不改請留空' : '沒有設就留空'}
            />
          </label>
          <div className="lg-options">
            <label className="lg-check">
              <input type="checkbox" checked={show} onChange={e => setShow(e.target.checked)} />
              顯示輸入的密碼
            </label>
            {entry?.has_protect && (
              <label className="lg-check">
                <input type="checkbox" checked={clearProtect} onChange={e => setClearProtect(e.target.checked)} />
                清除保護密碼
              </label>
            )}
          </div>
          <p className="lg-note">
            密碼用 Windows 加密存在這台電腦，只有目前的 Windows 帳號解得開；不會寫進紀錄或備份。
            同一個帳號的其他角色共用這組密碼。
          </p>
          {error && <p className="lg-error" role="alert">{error}</p>}
          <footer className="lg-actions">
            {entry && (
              <button type="button" className="lg-btn lg-remove" onClick={remove} disabled={busy}>
                從自動登入移除
              </button>
            )}
            <span className="lg-spacer" />
            <button type="button" className="lg-btn" onClick={onClose} disabled={busy}>取消</button>
            <button type="submit" className="lg-btn lg-primary" disabled={!canSave}>
              {busy ? '儲存中…' : '儲存'}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
