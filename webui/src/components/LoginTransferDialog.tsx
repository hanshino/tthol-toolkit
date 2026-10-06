import { useEffect, useId, useRef, useState } from 'react';
import { post, postBlob } from '../api/client';
import type { LoginImportResult } from '../api/types';
import { describeError, reportClientError } from '../diag/report';
import './login.css';

const MIN = 8;

// 帳號匯出 / 匯入: the list with its passwords, sealed with a passphrase the
// user picks, to carry to another computer (the Windows encryption here only
// opens on this machine).
export function LoginTransferDialog({ mode, onClose, onDone }: {
  mode: 'export' | 'import';
  onClose: () => void;
  onDone: () => void;
}) {
  const titleId = useId();
  const [pass, setPass] = useState('');
  const [again, setAgain] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => { first.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  const exporting = mode === 'export';
  const canGo = !busy && (exporting
    ? pass.length >= MIN && pass === again
    : file !== null && pass !== '');

  const go = async () => {
    if (!canGo) return;
    setBusy(true);
    setError(null);
    try {
      if (exporting) {
        const blob = await postBlob('/api/logins/export', { passphrase: pass });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'tthol-logins.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
        setResult('已匯出 tthol-logins.json。到另一台電腦的「派發」頁按「匯入帳號」，輸入同一組匯出密碼。');
      } else {
        const data = await file!.text();
        const r = await post<LoginImportResult>('/api/logins/import', { data, passphrase: pass, overwrite });
        setResult(`匯入完成：新增 ${r.added} 隻、更新 ${r.updated} 隻、略過 ${r.skipped} 隻。`);
        onDone();
      }
    } catch (e) {
      setError(`${exporting ? '匯出' : '匯入'}失敗：${describeError(e)}`);
      reportClientError(e, { component: `LoginTransferDialog.${mode}`, silent: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lg-backdrop" onMouseDown={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="lg-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header className="lg-head">
          <h2 id={titleId}>{exporting ? '匯出帳號' : '匯入帳號'}</h2>
        </header>
        <form className="lg-form" onSubmit={e => { e.preventDefault(); void go(); }}>
          {result ? (
            <p className="lg-done" role="status">{result}</p>
          ) : (
            <>
              {!exporting && (
                <label className="lg-field">
                  <span>匯出檔</span>
                  <input
                    ref={first} type="file" accept=".json,application/json" disabled={busy}
                    onChange={e => setFile(e.target.files?.[0] ?? null)}
                  />
                </label>
              )}
              <label className="lg-field">
                <span>匯出密碼</span>
                <input
                  ref={exporting ? first : undefined}
                  type={show ? 'text' : 'password'} value={pass} onChange={e => setPass(e.target.value)}
                  disabled={busy} autoComplete="new-password" maxLength={128}
                  placeholder={exporting ? `至少 ${MIN} 個字` : '匯出時設的那組'}
                />
              </label>
              {exporting && (
                <label className="lg-field">
                  <span>再輸入一次</span>
                  <input
                    type={show ? 'text' : 'password'} value={again} onChange={e => setAgain(e.target.value)}
                    disabled={busy} autoComplete="new-password" maxLength={128}
                  />
                </label>
              )}
              <div className="lg-options">
                <label className="lg-check">
                  <input type="checkbox" checked={show} onChange={e => setShow(e.target.checked)} />
                  顯示輸入的密碼
                </label>
                {!exporting && (
                  <label className="lg-check">
                    <input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} />
                    這台已有的角色也用檔案覆蓋
                  </label>
                )}
              </div>
              {exporting && pass !== '' && again !== '' && pass !== again && (
                <p className="lg-error">兩次輸入的匯出密碼不一樣</p>
              )}
              <p className="lg-note">
                {exporting
                  ? '檔案含所有角色的帳號、密碼和保護密碼，用這組匯出密碼加密；沒有它就打不開。匯出密碼不會存下來，忘了就只能重新匯出。'
                  : '匯入後的密碼改用這台電腦的 Windows 加密保存。'}
              </p>
            </>
          )}
          {error && <p className="lg-error" role="alert">{error}</p>}
          <footer className="lg-actions">
            <span className="lg-spacer" />
            {result ? (
              <button type="button" className="lg-btn lg-primary" onClick={onClose}>關閉</button>
            ) : (
              <>
                <button type="button" className="lg-btn" onClick={onClose} disabled={busy}>取消</button>
                <button type="submit" className="lg-btn lg-primary" disabled={!canGo}>
                  {busy ? '處理中…' : exporting ? '匯出' : '匯入'}
                </button>
              </>
            )}
          </footer>
        </form>
      </div>
    </div>
  );
}
