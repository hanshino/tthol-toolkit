import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type { SupplyBuyable, SupplyConfig, SupplyLoad, SupplyLogEntry, SupplyStatus, SupplyView } from '../../api/types';
import { MoneyInput } from '../../components/MoneyInput';
import { reportClientError } from '../../diag/report';
import './supply.css';

// 補給 (services/supply.py): one trip to a town shopkeeper that sells the
// 道具處置 "sell" items, stores the "store" items and buys the 補貨清單.
// 日常 modules run it first; the button here runs the same trip by hand.
const VIEW_MS = 5000;
const STATUS_MS = 1000;
const SAVE_DELAY_MS = 400;

const PHASE_LABEL: Record<SupplyLogEntry['phase'], string> = {
  sent: '送出', confirmed: '已確認', unconfirmed: '未確認', error: '錯誤', info: '',
};

const fmt = (n: number) => n.toLocaleString('en-US');
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
const clampInt = (v: string, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, Math.floor(Number(v)) || 0));

const toCfg = (c?: SupplyConfig): SupplyConfig => ({
  items: c?.items ?? [],
  keep_gold: c?.keep_gold ?? 100_000,
  extra_stop: c?.extra_stop ?? false,
  pet_summon: c?.pet_summon ?? true,
  stop_when_short: c?.stop_when_short ?? false,
  gold_low: c?.gold_low ?? null,
  gold_high: c?.gold_high ?? null,
  gold_target: c?.gold_target ?? 500_000,
});

export function SupplyPanel({ pid, active }: { pid: number; active: boolean }) {
  const [view, setView] = useState<SupplyView | null>(null);
  const [status, setStatus] = useState<SupplyStatus | null>(null);
  const [cfg, setCfg] = useState<SupplyConfig | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);
  const dirty = useRef(false);

  const loadView = useCallback(async () => {
    try {
      const v = await get<SupplyView>(`/api/characters/${pid}/supply`);
      setView(v);
      setStatus(v.status);
      if (!dirty.current) setCfg(toCfg(v.config));
    } catch (e) {
      reportClientError(e, { component: 'SupplyPanel.view', silent: true });
    }
  }, [pid]);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await get<SupplyStatus>(`/api/characters/${pid}/supply/status`));
    } catch (e) {
      reportClientError(e, { component: 'SupplyPanel.status', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    loadView();
    const v = window.setInterval(loadView, VIEW_MS);
    const s = window.setInterval(loadStatus, STATUS_MS);
    return () => { window.clearInterval(v); window.clearInterval(s); };
  }, [active, loadView, loadStatus]);

  const update = (next: SupplyConfig) => {
    setCfg(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/supply/config`, next);
        setNotice(null);
        dirty.current = false;
        loadView();
      } catch (e) {
        dirty.current = false;
        setNotice('設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'SupplyPanel.save', silent: true });
      }
    }, SAVE_DELAY_MS);
  };

  const toggle = async () => {
    setBusy(true);
    try {
      if (status?.running) {
        await post(`/api/characters/${pid}/supply/stop`);
      } else {
        const r = await post<{ ok: boolean; reason?: string | null }>(`/api/characters/${pid}/supply/start`);
        setNotice(r.ok ? null : r.reason ?? '無法開始');
      }
      await loadStatus();
    } catch (e) {
      reportClientError(e, { component: 'SupplyPanel.toggle' });
    } finally {
      setBusy(false);
    }
  };

  if (!view || !cfg) return <section className="gd-panel gd-dim">讀取補給設定…</section>;

  const running = !!status?.running;
  const rows = new Map(view.rows.map(r => [r.item_id, r]));
  const setItems = (items: SupplyConfig['items']) => update({ ...cfg, items });
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= cfg.items.length) return;
    const next = [...cfg.items];
    [next[i], next[j]] = [next[j], next[i]];
    setItems(next);
  };
  const unsold = view.rows.filter(r => r.unsold);
  const cost = cfg.items.reduce((sum, it) => {
    const r = rows.get(it.item_id);
    return sum + (r?.price ?? 0) * (r?.need ?? 0);
  }, 0);

  return (
    <div className="sp">
      <section className="gd-panel gd-strip" aria-label="補給">
        <span className={`gd-state${running ? ' is-on' : ''}`}>
          <i />{running ? (status?.host ? `${status.host}補給中` : '補給中') : '待命'}
        </span>
        <div className="gd-strip-text">
          <span className="gd-title">補給</span>
          <span className="gd-dim">先賣、再存、最後買到目標量；會走路到城鎮商人</span>
        </div>
        <div className="sp-hosts" title="哪些模組開始前會先補給">
          {view.hosts.length
            ? view.hosts.map(h => <span key={h} className="sp-chip">{h}</span>)
            : <span className="gd-dim">還沒有模組會呼叫補給</span>}
        </div>
        <button type="button" className={running ? '' : 'is-primary'} onClick={toggle}
          disabled={busy || (!running && !view.hook_ready)}>
          {running ? '停止' : '現在補給'}
        </button>
      </section>
      {notice && <div className="gd-notice" role="status">{notice}</div>}
      {!view.hook_ready && !running && (
        <div className="gd-notice">補給要透過 hook 走路、對話和買賣；這個遊戲視窗的 hook 沒有這些指令。設定仍可先編輯、儲存。</div>
      )}
      {running && status?.step && <div className="sp-step" role="status">{status.step}</div>}

      <section className="gd-panel sp-phases">
        <Phase n={1} title="賣掉" src="道具處置設成「賣掉」的道具，保留數量也在那裡設（行囊分頁點道具）">
          {view.sells.length === 0 ? (
            <div className="gd-dim sp-empty">沒有要賣的道具。</div>
          ) : (
            <MoveTable rows={view.sells} verb="要賣" />
          )}
        </Phase>
        <Phase n={2} title="存倉" src="道具處置設成「存倉」的道具；先到錢莊伙計存，再去商店"
          badge={view.store_supported ? undefined : '這個 hook 沒有存倉指令，會跳過'} dim={!view.store_supported}>
          {view.stores.length === 0 ? (
            <div className="gd-dim sp-empty">沒有要存的道具。</div>
          ) : (
            <MoveTable rows={view.stores} verb="要存" />
          )}
        </Phase>
        <Phase n={3} title="買到目標量" src="背包和寵物背包各補到目標；由上往下買，錢或空間不夠時下面的先放棄"
          badge={view.merchant ?? undefined} badgeTone="info">
          {unsold.length > 0 && (
            <div className="gd-notice">
              {view.merchant}沒賣：{unsold.map(r => r.name).join('、')}。有家族的角色只在家族商人補給，補給會在出門前停下並提示；把這些從清單移除，或換成家族商人有賣的。
            </div>
          )}
          {cfg.items.length > 0 && (
            <div className="sp-scroll">
              <table className="sp-table">
                <thead>
                  <tr>
                    <th>道具</th>
                    <th className="r">單價</th>
                    <th>背包目標</th>
                    <th>寵物背包目標</th>
                    <th className="r">要買</th>
                    <th className="r">花費</th>
                    <th aria-label="排序與移除" />
                  </tr>
                </thead>
                <tbody>
                  {cfg.items.map((it, i) => {
                    const r = rows.get(it.item_id);
                    const name = r?.name ?? `#${it.item_id}`;
                    const set = (patch: Partial<SupplyConfig['items'][number]>) =>
                      setItems(cfg.items.map((x, k) => (k === i ? { ...x, ...patch } : x)));
                    return (
                      <tr key={it.item_id} className={r?.unsold ? 'is-unsold' : undefined}>
                        <td>
                          <div className="sp-item">
                            {r?.icon_url ? <img src={r.icon_url} alt="" width={24} height={24} /> : <span className="sp-noicon" />}
                            <div>
                              <b>{name}</b>
                              <small>
                                {r?.unsold ? '家族商人沒賣' : r?.shops ? `${r.shops} 間城鎮商店有賣` : '城鎮商店沒賣'}
                              </small>
                            </div>
                          </div>
                        </td>
                        <td className="r gd-cnt">{r?.price != null ? fmt(r.price) : '—'}</td>
                        <td>
                          <span className="sp-split">
                            <input type="number" min={0} max={9999} value={it.bag} aria-label={`${name} 背包目標`}
                              onChange={e => set({ bag: clampInt(e.target.value, 0, 9999) })} />
                            <span className="gd-cnt">有 {r?.bag ?? 0}</span>
                          </span>
                        </td>
                        <td>
                          <span className="sp-split">
                            <input type="number" min={0} max={9999} value={it.pet} aria-label={`${name} 寵物背包目標`}
                              onChange={e => set({ pet: clampInt(e.target.value, 0, 9999) })} />
                            <span className="gd-cnt">有 {r?.pet ?? 0}</span>
                          </span>
                        </td>
                        <td className={`r gd-cnt${r?.need ? ' sp-need' : ''}`}>{r?.need ? fmt(r.need) : '足夠'}</td>
                        <td className="r gd-cnt">{r?.need && r.price ? fmt(r.need * r.price) : '—'}</td>
                        <td>
                          <span className="gd-ops">
                            <button type="button" aria-label={`${name} 往上`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                            <button type="button" aria-label={`${name} 往下`} disabled={i === cfg.items.length - 1} onClick={() => move(i, 1)}>↓</button>
                            <button type="button" aria-label={`移除 ${name}`} onClick={() => setItems(cfg.items.filter((_, k) => k !== i))}>×</button>
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <Picker pid={pid} family={view.family} taken={cfg.items.map(x => x.item_id)} onAdd={id => setItems([...cfg.items, { item_id: id, bag: 50, pet: 0 }])} />
        </Phase>
      </section>

      <div className="sp-lower">
        <section className="gd-panel sp-rules" aria-label="補給規則">
          <header className="gd-head"><h3>規則</h3></header>
          <label className="sp-rule">
            <span>銀兩至少留</span>
            <span className="sp-split">
              <MoneyInput value={cfg.keep_gold} aria-label="銀兩至少留"
                onChange={n => update({ ...cfg, keep_gold: n })} />
              <span className="gd-cnt">目前 {view.gold != null ? fmt(view.gold) : '讀不到'}</span>
            </span>
          </label>
          <Choice label="這間沒賣的道具" value={cfg.extra_stop}
            options={[[false, '跳過'], [true, '再找下一間']]} onChange={v => update({ ...cfg, extra_stop: v })} />
          <Choice label="寵物背包要放東西時" value={cfg.pet_summon}
            options={[[true, '自動召喚、放完收回'], [false, '只用已召喚的']]} onChange={v => update({ ...cfg, pet_summon: v })} />
          <Choice label="沒補齊（錢或空間不夠）" value={cfg.stop_when_short}
            options={[[false, '照常進行模組'], [true, '停下模組']]} onChange={v => update({ ...cfg, stop_when_short: v })} />
          <Bank cfg={cfg} view={view} onChange={update} />
        </section>

        <section className="gd-panel sp-preview" aria-label="這次補給預覽">
          <header className="gd-head">
            <h3>這次會怎麼跑</h3>
            {cost > 0 && <span className="gd-dim">買齊約 {fmt(cost)} 銀兩</span>}
          </header>
          {view.plan.length === 0 ? (
            <div className="gd-dim">沒有要賣或要買的東西，模組開始時不會出門補給。</div>
          ) : (
            <ol className="sp-plan">
              {view.plan.map((s, i) => (
                <li key={`${s.npc}-${i}`}>
                  <span className="sp-dot" />
                  <div>
                    <b>{s.stage_name}・{s.npc}</b> <span className="gd-cnt">({s.tile[0]}, {s.tile[1]})</span>
                    {s.actions.length > 0 && <div className="gd-dim">{s.actions.join('、')}</div>}
                    {s.actions.length === 0 && i === view.plan.findIndex(x => x.actions.length === 0) && view.sells.length > 0 && (
                      <div className="gd-dim">賣：{view.sells.map(x => `${x.name} ×${x.qty}`).join('、')}</div>
                    )}
                    {s.buys.length > 0 && <div className="gd-dim">買：{s.buys.join('、')}</div>}
                    {s.missing.length > 0 && <div className="sp-warn">這間沒賣：{s.missing.join('、')}</div>}
                  </div>
                </li>
              ))}
            </ol>
          )}
          {view.load && <Load load={view.load} />}
        </section>
      </div>

      <section className="gd-panel">
        <header className="gd-head">
          <h3>補給紀錄</h3>
          <span className="gd-dim">{status?.ended ? `上次：${status.ended}` : '最新的在下面'}</span>
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

function Phase({ n, title, src, badge, badgeTone = 'warn', dim, children }: {
  n: number; title: string; src: string; badge?: string; badgeTone?: 'warn' | 'info'; dim?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`sp-phase${dim ? ' is-dim' : ''}`}>
      <div className="sp-phase-head">
        <span className="sp-n">{n}</span>
        <h3>{title}</h3>
        <span className="gd-dim sp-src">{src}</span>
        {badge && <span className={`sp-badge is-${badgeTone}`}>{badge}</span>}
      </div>
      <div className="sp-phase-body">{children}</div>
    </div>
  );
}

function MoveTable({ rows, verb }: { rows: SupplyView['sells']; verb: string }) {
  return (
    <div className="sp-scroll">
      <table className="sp-table">
        <thead>
          <tr><th>道具</th><th className="r">身上</th><th className="r">保留</th><th className="r">{verb}</th></tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.item_id}>
              <td>
                <div className="sp-item">
                  {r.icon_url ? <img src={r.icon_url} alt="" width={24} height={24} /> : <span className="sp-noicon" />}
                  <b>{r.name}</b>
                </div>
              </td>
              <td className="r gd-cnt">{fmt(r.have)}</td>
              <td className="r gd-cnt">{fmt(r.keep)}</td>
              <td className="r gd-cnt sp-need">{fmt(r.qty)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Picker({ pid, family, taken, onAdd }: {
  pid: number; family: boolean; taken: number[]; onAdd: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<SupplyBuyable[]>([]);

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(async () => {
      try {
        setHits(await get<SupplyBuyable[]>(`/api/supply/items?pid=${pid}&q=${encodeURIComponent(q)}`));
      } catch (e) {
        reportClientError(e, { component: 'SupplyPanel.items', silent: true });
      }
    }, 200);
    return () => window.clearTimeout(t);
  }, [open, q, pid]);

  if (!open) {
    return <button type="button" className="gd-add" onClick={() => setOpen(true)}>＋ 加入道具</button>;
  }
  const shown = hits.filter(h => !taken.includes(h.item_id));
  return (
    <div className="gd-picker">
      <div className="gd-picker-head">
        <label className="sp-search">
          <span>道具名稱</span>
          <input type="text" value={q} autoFocus onChange={e => setQ(e.target.value)} />
        </label>
        <span>{family ? '只列你的家族商店有賣的道具' : '只列城鎮 NPC 用銀兩賣的道具'}</span>
        <button type="button" onClick={() => setOpen(false)}>關閉</button>
      </div>
      {shown.length === 0 ? (
        <div className="gd-dim gd-pad">沒有符合的道具。</div>
      ) : (
        <ul role="listbox" aria-label="加入補貨道具">
          {shown.map(h => (
            <li key={h.item_id}>
              <button type="button" role="option" aria-selected={false} onClick={() => onAdd(h.item_id)}>
                <span>{h.name}{h.type_label && <span className="gd-dim">　{h.type_label}</span>}</span>
                <span className="gd-cnt">{fmt(h.price)} 銀兩</span>
                <span className="gd-cnt">{h.shops} 間</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Choice<T extends boolean>({ label, value, options, onChange }: {
  label: string; value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <div className="sp-rule" role="radiogroup" aria-label={label}>
      <span>{label}</span>
      <span className="sp-radio">
        {options.map(([v, text]) => (
          <button key={String(v)} type="button" role="radio" aria-checked={value === v}
            className={value === v ? 'is-active' : ''} onClick={() => onChange(v)}>{text}</button>
        ))}
      </span>
    </div>
  );
}

function Bank({ cfg, view, onChange }: { cfg: SupplyConfig; view: SupplyView; onChange: (c: SupplyConfig) => void }) {
  const low = cfg.gold_low != null;
  const high = cfg.gold_high != null;
  return (
    <div className={`sp-bank${view.bank_supported ? '' : ' is-dim'}`}>
      <div className="sp-bank-head">
        <b>錢莊</b>
        {!view.bank_supported && <span className="sp-badge">這個 hook 沒有錢莊指令，會跳過</span>}
        <span className="gd-dim">在錢莊伙計的倉庫視窗存領，和存倉同一站</span>
      </div>
      <label className="sp-rule">
        <span>
          <input type="checkbox" checked={low}
            onChange={e => onChange({ ...cfg, gold_low: e.target.checked ? 100_000 : null })} />
          銀兩低於
        </span>
        <MoneyInput disabled={!low} value={cfg.gold_low ?? 0} aria-label="低於多少時領錢"
          onChange={n => onChange({ ...cfg, gold_low: n })} />
        <span>時領錢</span>
      </label>
      <label className="sp-rule">
        <span>
          <input type="checkbox" checked={high}
            onChange={e => onChange({ ...cfg, gold_high: e.target.checked ? 5_000_000 : null })} />
          銀兩高於
        </span>
        <MoneyInput disabled={!high} value={cfg.gold_high ?? 0} aria-label="高於多少時存錢"
          onChange={n => onChange({ ...cfg, gold_high: n })} />
        <span>時存錢</span>
      </label>
      <label className="sp-rule">
        <span>領或存到身上剩</span>
        <MoneyInput disabled={!low && !high} value={cfg.gold_target} aria-label="領錢或存錢後身上留多少"
          onChange={n => onChange({ ...cfg, gold_target: n })} />
      </label>
      {view.bank && <div className="gd-dim">現在會：{view.bank}</div>}
    </div>
  );
}

function Load({ load }: { load: SupplyLoad }) {
  const max = Math.max(load.weight_max, 1);
  const pct = (w: number) => Math.max(0, Math.min(100, (w * 100) / max));
  const over = load.weight_peak > load.weight_max;
  return (
    <div className="sp-load">
      <div className="sp-meter">
        <div className="sp-meter-head">
          <span>負重（估算）</span>
          <span className={`gd-cnt${over ? ' sp-warn' : ''}`}>
            {fmt(load.weight)} → {fmt(load.weight_after)} / {fmt(load.weight_max)}
          </span>
        </div>
        <div className="sp-bar" role="img"
          aria-label={`負重現在 ${Math.round(pct(load.weight))}%，補給後 ${Math.round(pct(load.weight_after))}%`}>
          <i className="sp-bar-now" style={{ width: `${pct(load.weight)}%` }} />
          <i className="sp-bar-after" style={{ left: `${pct(Math.min(load.weight, load.weight_after))}%`, width: `${Math.abs(pct(load.weight_after) - pct(load.weight))}%` }} />
        </div>
        {over && (
          <div className="sp-warn">買的時候最多到 {fmt(load.weight_peak)}，超過負重上限：調低目標，或讓寵物背包先有空間</div>
        )}
      </div>
      <div className="sp-slots">
        <span className={load.slots_after > load.slots_max ? 'sp-warn' : undefined}>
          背包 <b className="gd-cnt">{load.slots} → {load.slots_after}</b> / {load.slots_max} 格
        </span>
        <span className={load.pet_slots_after > load.pet_slots_max ? 'sp-warn' : undefined}>
          寵物背包 <b className="gd-cnt">{load.pet_slots} → {load.pet_slots_after}</b> / {load.pet_slots_max} 格
        </span>
      </div>
      {(load.slots_after > load.slots_max || load.pet_slots_after > load.pet_slots_max) && (
        <div className="sp-warn">補完放不下：調低目標，或先清出格子（賣掉、存倉）</div>
      )}
      <div className="gd-dim">以一格 200 個、道具資料庫的重量估算</div>
    </div>
  );
}
