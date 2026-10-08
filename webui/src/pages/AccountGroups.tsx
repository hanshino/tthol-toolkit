import { useCallback, useEffect, useState } from 'react';
import { del, get, post, put } from '../api/client';
import type { Account, AccountCharacter } from '../api/types';
import { describeError, reportClientError } from '../diag/report';
import { Panel } from '../primitives';
import './accounts.css';

// 帳號分組: characters of one game account share one warehouse, so the 寶庫
// counts only the newest warehouse record of each account, and 分身交貨 never
// trades between them. 帳號派發 characters on one login are grouped on their own.
const NEW = 'new';
const NONE = 'none';

export function AccountGroups() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [chars, setChars] = useState<AccountCharacter[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([
        get<Account[]>('/api/accounts'),
        get<AccountCharacter[]>('/api/accounts/characters'),
      ]);
      setAccounts(a);
      setChars(c);
    } catch (e) {
      setError(`讀取帳號失敗：${describeError(e)}`);
      reportClientError(e, { component: 'AccountGroups.load' });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(`${what}失敗：${describeError(e)}`);
      reportClientError(e, { component: `AccountGroups.${what}`, silent: true });
    } finally {
      setBusy(false);
      await load();
    }
  };

  const move = (character: string, to: string) => act('變更帳號', async () => {
    let account_id: number | null = null;
    if (to === NEW) {
      account_id = (await post<Account>('/api/accounts', { name: character })).account_id;
    } else if (to !== NONE) {
      account_id = Number(to);
    }
    await put(`/api/characters/by-name/${encodeURIComponent(character)}/account`, { account_id });
  });

  const rename = (a: Account, name: string) => {
    const n = name.trim();
    if (!n || n === a.name) return;
    act('改名', () => put(`/api/accounts/${a.account_id}`, { name: n }));
  };

  const groups: { account: Account | null; members: AccountCharacter[] }[] = [
    ...accounts.map(a => ({ account: a, members: chars.filter(c => c.account_id === a.account_id) })),
    { account: null, members: chars.filter(c => c.account_id == null) },
  ];

  return (
    <Panel title="帳號分組">
      <p className="ag-help">
        同一個遊戲帳號的角色共用倉庫，放在同一組後，寶庫只算這組最新的那份倉庫紀錄，不會重複計算。
        帳號派發裡用同一個登入帳號的角色會自動分在一起；其他的在這裡指定。
      </p>
      {error && <div className="ag-err" role="alert">{error}</div>}
      <div className="ag-grid">
        {groups.map(({ account, members }) => (
          <section key={account?.account_id ?? 'none'} className={`ag-card${account ? '' : ' is-none'}`}>
            <header className="ag-head">
              {account ? (
                <label className="ag-name">
                  <span className="ag-sr">帳號名稱</span>
                  <input
                    key={account.name} type="text" defaultValue={account.name} disabled={busy}
                    onBlur={e => rename(account, e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                  />
                </label>
              ) : <b className="ag-none">未分組</b>}
              <span className="ag-count">{members.length} 隻</span>
              {account && members.length === 0 && (
                <button type="button" className="ag-del" disabled={busy}
                  onClick={() => act('刪除帳號', () => del(`/api/accounts/${account.account_id}`))}>刪除</button>
              )}
            </header>
            {members.length === 0 ? (
              <div className="ag-empty">{account ? '沒有角色。' : '每隻角色都分好了。'}</div>
            ) : (
              <ul className="ag-list">
                {members.map(c => (
                  <li key={c.character}>
                    <span className="ag-char">{c.character}</span>
                    <label>
                      <span className="ag-sr">{c.character} 的帳號</span>
                      <select className="ag-sel" disabled={busy}
                        value={c.account_id == null ? NONE : String(c.account_id)}
                        onChange={e => move(c.character, e.target.value)}>
                        <option value={NONE}>未分組</option>
                        {accounts.map(a => <option key={a.account_id} value={String(a.account_id)}>{a.name}</option>)}
                        <option value={NEW}>＋ 新帳號</option>
                      </select>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
    </Panel>
  );
}
