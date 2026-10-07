import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type {
  HandoffConfig, HandoffLogEntry, HandoffReceiver, HandoffStatus, HandoffView, ItemMeta,
} from '../../api/types';
import { useItemMeta } from '../../components/items/useItemMeta';
import { reportClientError } from '../../diag/report';
import './handoff.css';

// 分身交貨 (services/handoff.py): one character stands as a warehouse and takes
// whitelisted items from the other windows by trade, a round at a time; each
// round's 存倉 items are stored before the next one.
const VIEW_MS = 5000;
const STATUS_MS = 1000;
const SAVE_DELAY_MS = 400;
type Role = 'receive' | 'send';

const PHASE_LABEL: Record<HandoffLogEntry['phase'], string> = {
  confirmed: '已確認', unconfirmed: '未確認', error: '錯誤', info: '',
};
const STATE_LABEL: Record<HandoffReceiver['state'], string> = {
  waiting: '等待送貨', trading: '交易中', storing: '存倉中', busy: '準備中', stopped: '已停止',
};

const fmt = (n: number) => n.toLocaleString('en-US');
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
const toCfg = (c?: HandoffConfig): HandoffConfig => ({
  items: c?.items ?? [], bag_slots: c?.bag_slots ?? 40, from_warehouse: c?.from_warehouse ?? true,
});

export function HandoffPanel({ pid, active }: { pid: number; active: boolean }) {
  const [view, setView] = useState<HandoffView | null>(null);
  const [status, setStatus] = useState<HandoffStatus | null>(null);
  const [cfg, setCfg] = useState<HandoffConfig | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);
  const dirty = useRef(false);

  const loadView = useCallback(async () => {
    try {
      const v = await get<HandoffView>(`/api/characters/${pid}/handoff`);
      setView(v);
      setStatus(v.status);
      if (!dirty.current) setCfg(toCfg(v.config));
    } catch (e) {
      reportClientError(e, { component: 'HandoffPanel.view', silent: true });
    }
  }, [pid]);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await get<HandoffStatus>(`/api/characters/${pid}/handoff/status`));
    } catch (e) {
      reportClientError(e, { component: 'HandoffPanel.status', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    loadView();
    const v = window.setInterval(loadView, VIEW_MS);
    const s = window.setInterval(loadStatus, STATUS_MS);
    return () => { window.clearInterval(v); window.clearInterval(s); };
  }, [active, loadView, loadStatus]);

  const metas = useItemMeta(cfg?.items.map(i => i.item_id) ?? []);

  const update = (next: HandoffConfig) => {
    setCfg(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/handoff/config`, next);
        setNotice(null);
        dirty.current = false;
        loadView();
      } catch (e) {
        dirty.current = false;
        setNotice('設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'HandoffPanel.save', silent: true });
      }
    }, SAVE_DELAY_MS);
  };

  if (!view || !cfg) return <section className="gd-panel gd-dim">讀取分身交貨…</section>;

  const running = !!status?.running;
  const shown: Role = (running && status?.role) || role || (cfg.items.length ? 'receive' : 'send');

  const toggle = async () => {
    setBusy(true);
    try {
      if (running) {
        await post(`/api/characters/${pid}/handoff/stop`);
      } else {
        const r = await post<{ ok: boolean; reason?: string | null }>(
          `/api/characters/${pid}/handoff/start`, { role: shown });
        setNotice(r.ok ? null : r.reason ?? '無法開始');
      }
      await loadView();
    } catch (e) {
      reportClientError(e, { component: 'HandoffPanel.toggle' });
    } finally {
      setBusy(false);
    }
  };

  const startLabel = shown === 'receive' ? '開始收貨' : '開始送貨';
  const stopLabel = status?.stopping ? '馬上停止' : shown === 'receive' ? '停止收貨' : '停止送貨';
  const stateText = running
    ? status?.stopping ? '這輪存完就停' : shown === 'receive' ? '收貨中' : '送貨中'
    : '待命';

  return (
    <div className="ho">
      <section className="gd-panel gd-strip" aria-label="分身交貨">
        <span className={`gd-state${running ? ' is-on' : ''}${status?.stopping ? ' is-warn' : ''}`}>
          <i />{stateText}
        </span>
        <div className="gd-strip-text">
          <span className="gd-title">分身交貨</span>
          <span className="gd-dim">不同帳號的角色互相交易：一隻當倉庫收，其他視窗走過去交</span>
        </div>
        <div className="sp-radio" role="radiogroup" aria-label="這隻角色的角色">
          {(['receive', 'send'] as Role[]).map(r => (
            <button key={r} type="button" role="radio" aria-checked={shown === r}
              className={shown === r ? 'is-active' : ''} disabled={running}
              onClick={() => setRole(r)}>
              {r === 'receive' ? '收貨（當倉庫）' : '送貨'}
            </button>
          ))}
        </div>
        <button type="button" className={running ? '' : 'is-primary'} onClick={toggle} disabled={busy}>
          {running ? stopLabel : startLabel}
        </button>
      </section>
      {notice && <div className="gd-notice" role="status">{notice}</div>}
      {running && status?.step && <div className="sp-step" role="status">{status.step}</div>}

      {shown === 'receive'
        ? <Receiver view={view} cfg={cfg} status={status} metas={metas} onChange={update} />
        : <Sender view={view} cfg={cfg} status={status} onChange={update} />}

      <section className="gd-panel">
        <header className="gd-head">
          <h3>交貨紀錄</h3>
          <span className="gd-dim">{status?.ended ? `上次：${status.ended}` : '最新的在上面'}</span>
        </header>
        {!status || status.log.length === 0 ? (
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

function Receiver({ view, cfg, status, metas, onChange }: {
  view: HandoffView; cfg: HandoffConfig; status: HandoffStatus | null;
  metas: Map<number, ItemMeta>; onChange: (c: HandoffConfig) => void;
}) {
  const running = !!status?.running;
  const used = view.bag_used;
  const free = used == null ? null : cfg.bag_slots - used;
  const setItems = (items: HandoffConfig['items']) => onChange({ ...cfg, items });
  const taken = new Set(cfg.items.map(i => i.item_id));
  const names = new Map([...view.bag, ...(view.warehouse ?? [])].map(b => [b.item_id, b.name]));

  return (
    <>
      <section className="gd-panel ho-stats" aria-label="收貨狀態">
        <div>
          <span className="ho-lbl">背包</span>
          <span className="ho-num">{used ?? '—'} / {cfg.bag_slots}
            {free != null && <small className={free > 0 ? 'ho-ok' : 'ho-bad'}>空 {free} 格</small>}
          </span>
        </div>
        <label className="ho-slots">
          <span className="ho-lbl">背包格數</span>
          <input type="number" min={1} max={200} value={cfg.bag_slots} disabled={running}
            onChange={e => onChange({ ...cfg, bag_slots: Math.min(200, Math.max(1, Math.floor(Number(e.target.value)) || 1)) })} />
        </label>
        <div>
          <span className="ho-lbl">正在交易</span>
          <span className="ho-val">{status?.turn_with ?? '—'}</span>
        </div>
        <div>
          <span className="ho-lbl">排隊</span>
          <span className="ho-val">{status?.queue.length ? status.queue.join('、') : '—'}</span>
        </div>
      </section>

      <section className="gd-panel">
        <header className="gd-head">
          <h3>收哪些東西</h3>
          <span className="gd-dim">送貨方只會交出名單上的東西；「馬上存倉」的每收一輪就存掉</span>
        </header>
        {cfg.items.length === 0 ? (
          <div className="gd-dim ho-empty">還沒有道具。從下面搜尋、背包或倉庫挑。</div>
        ) : (
          <Whitelist cfg={cfg} metas={metas} names={names} onChange={setItems} />
        )}
        <Picker view={view} taken={taken} onAdd={(ids, store) =>
          setItems([...cfg.items, ...ids.filter(id => !taken.has(id)).map(id => ({ item_id: id, store }))])} />
      </section>

      {status?.moved.length ? (
        <section className="gd-panel">
          <header className="gd-head"><h3>這次開著以來收到</h3></header>
          <ul className="ho-moved">
            {status.moved.map(c => (
              <li key={c.item_id}>
                <span>{c.name}</span>
                <span className="gd-cnt">{fmt(c.count)}</span>
                <span className={c.store ? 'ho-store' : 'ho-keep'}>{c.store ? '已存倉' : '在身上'}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="gd-notice ho-rules">
        只接受這個工具正在送貨的角色，其他人的邀請一律不理。建議站在倉庫管理員旁邊，每輪存完會走回原位。開著就一直等，直到你按停止。
      </div>
    </>
  );
}

type Category = ItemMeta['category'];
const CATEGORIES: [Category | '', string][] = [
  ['', '全部類別'], ['book', '技能書'], ['gear', '裝備'], ['potion', '藥品'],
  ['event', '活動'], ['pet', '寵物'], ['misc', '其他'],
];
type Source = 'search' | 'bag' | 'warehouse';
type Pick = { item_id: number; name: string; note: string; no_store: boolean };

function Whitelist({ cfg, metas, names, onChange }: {
  cfg: HandoffConfig; metas: Map<number, ItemMeta>; names: Map<number, string>;
  onChange: (items: HandoffConfig['items']) => void;
}) {
  const [filter, setFilter] = useState('');
  const nameOf = (id: number) => metas.get(id)?.name ?? names.get(id) ?? `#${id}`;
  const f = filter.trim();
  const shown = cfg.items.filter(it => !f || nameOf(it.item_id).includes(f));
  const flip = (id: number) => onChange(cfg.items.map(x => (x.item_id === id ? { ...x, store: !x.store } : x)));
  const drop = (id: number) => onChange(cfg.items.filter(x => x.item_id !== id));
  const column = (store: boolean) => {
    const list = shown.filter(it => it.store === store);
    const all = cfg.items.filter(it => it.store === store).length;
    return (
      <div className={`ho-col ${store ? 'is-store' : 'is-keep'}`}>
        <div className="ho-col-head">
          <b>{store ? '馬上存倉' : '留在身上'}</b>
          <span className="gd-dim">{f ? `${list.length} / ${all}` : all} 項</span>
          {list.length > 0 && (
            <button type="button" className="ho-mini" onClick={() =>
              onChange(cfg.items.map(x => (list.some(l => l.item_id === x.item_id) ? { ...x, store: !store } : x)))}>
              {f ? '這些' : '全部'}移到{store ? '留在身上' : '馬上存倉'}
            </button>
          )}
        </div>
        {list.length === 0 ? (
          <div className="gd-dim ho-empty">{f ? '沒有符合的。' : '沒有。'}</div>
        ) : (
          <ul className="ho-chips">
            {list.map(it => {
              const m = metas.get(it.item_id);
              const name = nameOf(it.item_id);
              const warn = m?.no_trade ? '不能交易' : store && m?.no_store ? '不能存倉' : null;
              return (
                <li key={it.item_id} className={warn ? 'is-warn' : undefined} title={warn ?? m?.type_label ?? ''}>
                  <span className="ho-chip-name">{name}</span>
                  <button type="button" className="ho-chip-btn" aria-label={`${name} 改成${store ? '留在身上' : '馬上存倉'}`}
                    title={store ? '改成留在身上' : '改成馬上存倉'} onClick={() => flip(it.item_id)}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M7 7h11l-3-3M17 17H6l3 3" />
                    </svg>
                  </button>
                  <button type="button" className="ho-chip-btn is-x" aria-label={`移除 ${name}`} title="移除" onClick={() => drop(it.item_id)}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    );
  };
  return (
    <>
      {cfg.items.length > 8 && (
        <label className="sp-search ho-filter">
          <span>篩選名單</span>
          <input type="text" value={filter} placeholder="例如 11級" onChange={e => setFilter(e.target.value)} />
        </label>
      )}
      <div className="ho-board">
        {column(true)}
        {column(false)}
      </div>
    </>
  );
}

function Picker({ view, taken, onAdd }: {
  view: HandoffView; taken: Set<number>; onAdd: (ids: number[], store: boolean) => void;
}) {
  const [source, setSource] = useState<Source | null>(null);
  const [q, setQ] = useState('');
  const [cat, setCat] = useState<Category | ''>('book');
  const [hits, setHits] = useState<ItemMeta[]>([]);

  useEffect(() => {
    if (source !== 'search' || (!q.trim() && !cat)) { setHits([]); return; }
    const t = window.setTimeout(async () => {
      try {
        const params = new URLSearchParams({ tradable: 'true', limit: '1000', q: q.trim() });
        if (cat) params.set('category', cat);
        setHits(await get<ItemMeta[]>(`/api/items/search?${params}`));
      } catch (e) {
        reportClientError(e, { component: 'HandoffPanel.search', silent: true });
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [source, q, cat]);

  const SOURCES: [Source, string][] = [['search', '搜尋道具'], ['bag', `從背包挑（${view.bag.length}）`], ['warehouse', `從倉庫挑（${(view.warehouse ?? []).length}）`]];
  if (source === null) {
    return (
      <div className="ho-add">
        {SOURCES.map(([s, label]) => (
          <button key={s} type="button" className="gd-add" onClick={() => setSource(s)}>＋ {label}</button>
        ))}
      </div>
    );
  }
  const f = q.trim();
  const fromHeld = (rows: HandoffView['bag']): Pick[] => rows
    .filter(r => !r.no_trade && (!cat || r.category === cat) && (!f || r.name.includes(f)))
    .map(r => ({ item_id: r.item_id, name: r.name, note: `${fmt(r.count)}（${r.stacks} 格）`, no_store: r.no_store }));
  const rows: Pick[] = source === 'search'
    ? hits.map(h => ({ item_id: h.item_id, name: h.name, note: h.type_label, no_store: h.no_store }))
    : fromHeld(source === 'bag' ? view.bag : (view.warehouse ?? []));
  const fresh = rows.filter(r => !taken.has(r.item_id));
  const empty = source === 'search' && !f && !cat ? '輸入名稱，或選一個類別。'
    : source === 'warehouse' && (view.warehouse ?? []).length === 0 ? '還沒讀到倉庫：在遊戲裡打開一次這隻角色的倉庫（看一眼就好），再回來這裡。'
    : '沒有符合、還沒加入的道具。';
  return (
    <div className="gd-picker ho-picker">
      <div className="gd-picker-head ho-picker-head">
        <span className="sp-radio" role="radiogroup" aria-label="從哪裡挑">
          {SOURCES.map(([s, label]) => (
            <button key={s} type="button" role="radio" aria-checked={source === s}
              className={source === s ? 'is-active' : ''} onClick={() => setSource(s)}>{label}</button>
          ))}
        </span>
        <button type="button" onClick={() => setSource(null)}>關閉</button>
      </div>
      <div className="ho-picker-tools">
        <label className="sp-search">
          <span>名稱</span>
          <input type="text" value={q} autoFocus placeholder="例如 刀、11級" onChange={e => setQ(e.target.value)} />
        </label>
        <label className="sp-search">
          <span>類別</span>
          <select className="dl-select" value={cat} onChange={e => setCat(e.target.value as Category | '')}>
            {CATEGORIES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
        <span className="gd-dim">{fresh.length} 項可加入{rows.length > fresh.length ? `（另有 ${rows.length - fresh.length} 項已在名單）` : ''}</span>
        {fresh.length > 0 && (
          <span className="ho-bulk">
            <button type="button" onClick={() => onAdd(fresh.map(r => r.item_id), true)}>全部加入 → 存倉</button>
            <button type="button" className="ho-keep" onClick={() => onAdd(fresh.map(r => r.item_id), false)}>全部加入 → 留身上</button>
          </span>
        )}
      </div>
      {fresh.length === 0 ? (
        <div className="gd-dim gd-pad">{empty}</div>
      ) : (
        <ul className="ho-pick-list" aria-label="可加入的道具">
          {fresh.map(r => (
            <li key={r.item_id}>
              <span className="ho-chip-name">{r.name}</span>
              <span className="gd-dim">{r.note}{r.no_store ? '　不能存倉' : ''}</span>
              <button type="button" className="ho-mini" onClick={() => onAdd([r.item_id], true)}>存倉</button>
              <button type="button" className="ho-mini ho-keep" onClick={() => onAdd([r.item_id], false)}>留身上</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Sender({ view, cfg, status, onChange }: {
  view: HandoffView; cfg: HandoffConfig; status: HandoffStatus | null; onChange: (c: HandoffConfig) => void;
}) {
  const running = !!status?.running;
  const open = view.receivers;
  const usable = open.filter(r => !r.same_account);
  const total = view.plan.length;

  return (
    <>
      <section className="gd-panel">
        <header className="gd-head">
          <h3>開著的倉庫</h3>
          <span className="gd-dim">照開始收貨的順序一個一個交；同帳號的不能交易</span>
        </header>
        {open.length === 0 ? (
          <div className="gd-dim ho-empty">沒有角色在收貨。先在要當倉庫的角色按「開始收貨」。</div>
        ) : (
          <ol className="ho-recv">
            {open.map((r, i) => (
              <li key={r.pid} data-state={r.same_account ? 'same' : r.state}
                className={status?.turn_with === r.character ? 'is-now' : undefined}>
                <div className="ho-recv-head">
                  <b>{i + 1} · {r.character}</b>
                  <span className="ho-recv-state">
                    {r.same_account ? '同帳號，不能交易' : r.busy_with ? `${STATE_LABEL[r.state]}（${r.busy_with}）` : STATE_LABEL[r.state]}
                  </span>
                </div>
                <div className="gd-dim">
                  {r.stage_name ?? '—'}{r.tile ? ` (${r.tile[0]}, ${r.tile[1]})` : ''}
                  {' · '}空 {r.free ?? '—'} 格 · 收 {r.items} 項{r.queue ? ` · 排隊 ${r.queue}` : ''}
                </div>
                {!r.same_account && <div>我要交給它 <span className="gd-cnt">{r.stacks}</span> 格</div>}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="gd-panel">
        <header className="gd-head">
          <h3>會交出去的東西</h3>
          <span className="gd-dim">背包裡符合某個倉庫白名單的，共 {total} 格；其他都不動</span>
        </header>
        {total === 0 ? (
          <div className="gd-dim ho-empty">{usable.length ? '背包裡沒有倉庫要收的東西。' : '還沒有可以交的倉庫。'}</div>
        ) : (
          <div className="sp-scroll">
            <table className="sp-table">
              <thead>
                <tr><th>道具</th><th className="r">數量</th><th>交給</th><th>對方收到後</th></tr>
              </thead>
              <tbody>
                {view.plan.map((p, i) => (
                  <tr key={`${p.item_id}-${i}`}>
                    <td><b className="ho-name">{p.name}</b></td>
                    <td className="r gd-cnt">{fmt(p.count)}</td>
                    <td>{p.receiver}</td>
                    <td className={p.store ? 'ho-store' : 'ho-keep'}>{p.store ? '存倉' : '留在身上'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="gd-dim ho-foot">
          每一輪只放對方空的格數（也不超過一次交易能放的格數）；對方收下、存完倉，才放下一輪。
        </div>
        <div className="ho-wh">
          <label className="dl-check">
            <input type="checkbox" checked={cfg.from_warehouse} disabled={running}
              onChange={e => onChange({ ...cfg, from_warehouse: e.target.checked })} />
            背包交完，也去自己的倉庫領出來交
          </label>
          {cfg.from_warehouse && (
            <div className="ho-wh-row">
              <span className="gd-dim">一次領到背包滿（這隻角色背包</span>
              <label className="ho-slots">
                <span className="sr-only">送貨角色背包格數</span>
                <input type="number" min={1} max={200} value={cfg.bag_slots} disabled={running}
                  onChange={e => onChange({ ...cfg, bag_slots: Math.min(200, Math.max(1, Math.floor(Number(e.target.value)) || 1)) })} />
              </label>
              <span className="gd-dim">格），交完再回去領，直到倉庫沒有倉庫要收的東西；上面列的只算背包</span>
            </div>
          )}
        </div>
      </section>

      {status?.moved.length ? (
        <section className="gd-panel">
          <header className="gd-head"><h3>這次交出去的</h3></header>
          <ul className="ho-moved">
            {status.moved.map(c => (
              <li key={c.item_id}><span>{c.name}</span><span className="gd-cnt">{fmt(c.count)}</span><span /></li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
