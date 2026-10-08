import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type { SupplyLogEntry, SupplyStatus, WithdrawConfig, WithdrawView } from '../../api/types';
import { reportClientError } from '../../diag/report';
import { ItemPicker, type PickAction } from './ItemPicker';
import { stateClass, WITHDRAW_HOST, withdrawState } from './assistState';
import './withdraw.css';

// 領倉白名單 (services/withdraw.py): take the listed items out of the account's
// warehouse onto this character, whole stacks, as many as the bag has room for.
// The trip is 補給's, so its status and log are the supply ones.
const VIEW_MS = 5000;
const STATUS_MS = 1000;
const SAVE_DELAY_MS = 400;
const PICK_ACTIONS: PickAction[] = [{ key: 'add', label: '加入', bulk: '全部加入' }];

const PHASE_LABEL: Record<SupplyLogEntry['phase'], string> = {
  sent: '送出', confirmed: '已確認', unconfirmed: '未確認', error: '錯誤', info: '',
};

const fmt = (n: number) => n.toLocaleString('en-US');
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
const when = (iso: string) => iso.slice(5, 16).replace('-', '/').replace('T', ' ');
const toCfg = (c?: WithdrawConfig): WithdrawConfig => ({ items: c?.items ?? [], bag_slots: c?.bag_slots ?? 40 });

export function WithdrawPanel({ pid, active }: { pid: number; active: boolean }) {
  const [view, setView] = useState<WithdrawView | null>(null);
  const [status, setStatus] = useState<SupplyStatus | null>(null);
  const [cfg, setCfg] = useState<WithdrawConfig | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);
  const dirty = useRef(false);

  const loadView = useCallback(async () => {
    try {
      const v = await get<WithdrawView>(`/api/characters/${pid}/withdraw`);
      setView(v);
      setStatus(v.status);
      if (!dirty.current) setCfg(toCfg(v.config));
    } catch (e) {
      reportClientError(e, { component: 'WithdrawPanel.view', silent: true });
    }
  }, [pid]);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await get<SupplyStatus>(`/api/characters/${pid}/supply/status`));
    } catch (e) {
      reportClientError(e, { component: 'WithdrawPanel.status', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    loadView();
    const v = window.setInterval(loadView, VIEW_MS);
    const s = window.setInterval(loadStatus, STATUS_MS);
    return () => { window.clearInterval(v); window.clearInterval(s); };
  }, [active, loadView, loadStatus]);

  const update = (next: WithdrawConfig) => {
    setCfg(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/withdraw/config`, next);
        setNotice(null);
        dirty.current = false;
        loadView();
      } catch (e) {
        dirty.current = false;
        setNotice('設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'WithdrawPanel.save', silent: true });
      }
    }, SAVE_DELAY_MS);
  };

  if (!view || !cfg) return <section className="gd-panel gd-dim">讀取領倉白名單…</section>;

  const mine = !!status?.running && status.host === WITHDRAW_HOST;
  const other = !!status?.running && !mine;
  const state = withdrawState(status);

  const toggle = async () => {
    setBusy(true);
    try {
      if (mine) {
        await post(`/api/characters/${pid}/withdraw/stop`);
      } else {
        const r = await post<{ ok: boolean; reason?: string | null }>(`/api/characters/${pid}/withdraw/start`);
        setNotice(r.ok ? null : r.reason ?? '無法開始');
      }
      await loadView();
    } catch (e) {
      reportClientError(e, { component: 'WithdrawPanel.toggle' });
    } finally {
      setBusy(false);
    }
  };

  const used = view.bag_used;
  const free = used == null ? null : cfg.bag_slots - used;
  const taken = new Set(cfg.items);
  const rowOf = new Map(view.rows.map(r => [r.item_id, r]));
  const known = view.warehouse_from !== null;
  const stacks = view.rows.reduce((n, r) => n + r.stacks, 0);
  const drop = (id: number) => update({ ...cfg, items: cfg.items.filter(x => x !== id) });
  const source = view.warehouse_from === 'snapshot'
    ? `${view.warehouse_holder} ${view.warehouse_at ? when(view.warehouse_at) : ''} 的紀錄`
    : view.warehouse_from === 'live' ? '這個視窗上次打開時看到的' : '還沒有紀錄';
  const showLog = !!status && (mine || status.last_host === WITHDRAW_HOST);

  return (
    <div className="ho">
      <section className="gd-panel gd-strip" aria-label="領倉白名單">
        <span className={stateClass(state)}><i />{state.text}</span>
        <div className="gd-strip-text">
          <span className="gd-title">領倉白名單</span>
          <span className="gd-dim">把同帳號倉庫裡名單上的道具整疊領到這隻角色身上，背包滿就停</span>
        </div>
        <button type="button" className={mine ? '' : 'is-primary'} onClick={toggle}
          disabled={busy || other || (!mine && (!view.hook_ready || cfg.items.length === 0))}>
          {mine ? '停止' : '開始領倉'}
        </button>
      </section>
      {notice && <div className="gd-notice" role="status">{notice}</div>}
      {other && <div className="gd-notice" role="status">{status?.host ?? '補給'}正在跑，跑完才能領倉。</div>}
      {!view.hook_ready && <div className="gd-notice">這個視窗的 hook 沒有領倉要用的指令（warehouse / withdraw / bag）。</div>}
      {mine && status?.step && <div className="sp-step" role="status">{status.step}</div>}

      <section className="gd-panel ho-stats" aria-label="領倉狀態">
        <div>
          <span className="ho-lbl">背包</span>
          <span className="ho-num">{used ?? '—'} / {cfg.bag_slots}
            {free != null && <small className={free > 0 ? 'ho-ok' : 'ho-bad'}>空 {free} 格</small>}
          </span>
        </div>
        <label className="ho-slots">
          <span className="ho-lbl">背包格數</span>
          <input type="number" min={1} max={200} value={cfg.bag_slots} disabled={mine}
            onChange={e => update({ ...cfg, bag_slots: Math.min(200, Math.max(1, Math.floor(Number(e.target.value)) || 1)) })} />
        </label>
        <div>
          <span className="ho-lbl">帳號</span>
          <span className="ho-val" title={view.account ?? undefined}>{view.account ?? '未分組（到「留影」頁設定）'}</span>
        </div>
        <div>
          <span className="ho-lbl">倉庫數量來自</span>
          <span className="ho-val" title={source}>{source}</span>
        </div>
      </section>

      <section className="gd-panel">
        <header className="gd-head">
          <h3>要領的道具</h3>
          <span className="gd-dim">
            {known && cfg.items.length > 0
              ? `倉庫裡有 ${stacks} 堆${free != null && stacks > free ? `，背包只放得下 ${Math.max(free, 0)} 堆` : ''}`
              : '領整疊：一堆佔一格'}
          </span>
        </header>
        {cfg.items.length === 0 ? (
          <div className="gd-dim ho-empty">還沒有道具。從下面搜尋、背包或倉庫挑；身上帶著要領的東西時，「從背包挑」最快。</div>
        ) : (
          <ul className="wd-list" aria-label="白名單">
            <li className="wd-head" aria-hidden="true"><span>道具</span><span>身上</span><span>倉庫</span><span /></li>
            {cfg.items.map(id => {
              const r = rowOf.get(id);
              const name = r?.name ?? `#${id}`;
              return (
                <li key={id} className={r && r.stacks > 0 ? 'is-there' : undefined}>
                  <span className="ho-chip-name">{name}</span>
                  <span className="gd-cnt">{r ? fmt(r.bag) : '—'}</span>
                  <span className="gd-cnt">
                    {r?.warehouse == null ? '—' : r.warehouse === 0 ? '0' : `${fmt(r.warehouse)}（${r.stacks} 堆）`}
                  </span>
                  <button type="button" className="ho-chip-btn is-x" aria-label={`移除 ${name}`} title="移除"
                    disabled={mine} onClick={() => drop(id)}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <ItemPicker bag={view.bag} warehouse={view.warehouse} taken={taken} actions={PICK_ACTIONS}
          warehouseEmpty="還沒有這個帳號的倉庫紀錄：在遊戲裡打開一次倉庫，或在留影頁記錄倉庫。"
          onAdd={ids => update({ ...cfg, items: [...cfg.items, ...ids.filter(id => !taken.has(id))] })} />
      </section>

      <section className="gd-panel">
        <header className="gd-head">
          <h3>領倉紀錄</h3>
          <span className="gd-dim">{showLog && status?.ended ? `上次：${status.ended}` : '最新的在下面'}</span>
        </header>
        {!showLog || !status || status.log.length === 0 ? (
          <div className="gd-dim">還沒有紀錄。</div>
        ) : (
          <ul className="gd-log">
            {status.log.map(e => (
              <li key={e.id} data-phase={e.phase}>
                <span className="gd-t">{clock(e.ts)}</span>
                <span className="gd-ph">{PHASE_LABEL[e.phase]}</span>
                <span className="gd-what">{e.text}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
