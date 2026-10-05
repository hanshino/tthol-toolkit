import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type { GuardConfig, GuardLogEntry, GuardStatus, GuardVitals, PotionCandidate } from '../../api/types';
import { reportClientError } from '../../diag/report';
import './guard.css';

// Standing guard (services/guard.py): rules that only use items, so they can
// run while the user plays by hand. Needs the hook's command pipe.
const POLL_MS = 1000;
const SAVE_DELAY_MS = 400;

type Resource = 'hp' | 'mp';
const RES_LABEL: Record<Resource, string> = { hp: '體力', mp: '真氣' };
const PHASE_LABEL: Record<GuardLogEntry['phase'], string> = {
  sent: '送出', confirmed: '已確認', unconfirmed: '未確認', error: '錯誤', info: '',
};

type Rule = { hp_pct: number; mp_pct: number; hp_items: number[]; mp_items: number[] };

const toRule = (c?: GuardConfig): Rule => ({
  hp_pct: c?.potion.hp_pct ?? 70,
  mp_pct: c?.potion.mp_pct ?? 30,
  hp_items: c?.potion.hp_items ?? [],
  mp_items: c?.potion.mp_items ?? [],
});

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });

export function GuardPanel({ pid, active }: { pid: number; active: boolean }) {
  const [status, setStatus] = useState<GuardStatus | null>(null);
  const [rule, setRule] = useState<Rule | null>(null);
  const [potions, setPotions] = useState<PotionCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const saveTimer = useRef<number | null>(null);
  const dirty = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const s = await get<GuardStatus>(`/api/characters/${pid}/guard`);
      setStatus(s);
      // Do not overwrite an edit that is waiting to be saved.
      if (!dirty.current) setRule(toRule(s.config));
    } catch (e) {
      reportClientError(e, { component: 'GuardPanel.refresh', silent: true });
    }
  }, [pid]);

  const loadPotions = useCallback(async () => {
    try {
      setPotions(await get<PotionCandidate[]>(`/api/characters/${pid}/guard/potions`));
    } catch (e) {
      reportClientError(e, { component: 'GuardPanel.potions', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    refresh();
    loadPotions();
    const t = window.setInterval(refresh, POLL_MS);
    const p = window.setInterval(loadPotions, 5000);
    return () => { window.clearInterval(t); window.clearInterval(p); };
  }, [active, refresh, loadPotions]);

  const update = (next: Rule) => {
    setRule(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/guard/config`, { potion: next });
        setNotice(null);
      } catch (e) {
        setNotice('設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'GuardPanel.save', silent: true });
      } finally {
        dirty.current = false;
      }
    }, SAVE_DELAY_MS);
  };

  const toggle = async () => {
    if (!status) return;
    setBusy(true);
    try {
      if (status.running) {
        await post(`/api/characters/${pid}/guard/stop`);
        setNotice(null);
      } else {
        const r = await post<{ ok: boolean; reason?: string | null }>(`/api/characters/${pid}/guard/start`);
        setNotice(r.ok ? null : r.reason ?? '無法啟動');
      }
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'GuardPanel.toggle' });
    } finally {
      setBusy(false);
    }
  };

  if (!status || !rule) return <section className="gd-panel gd-dim">讀取守護設定…</section>;

  const byId = new Map(potions.map(p => [p.item_id, p]));
  const running = status.running;

  return (
    <div className="gd">
      <section className="gd-panel gd-strip" aria-label="常駐守護">
        <span className={`gd-state${running ? ' is-on' : ''}${running && status.problem ? ' is-warn' : ''}`}>
          <i />{running ? (status.problem ? '等待中' : '守護中') : '已停止'}
        </span>
        <div className="gd-strip-text">
          <span className="gd-title">常駐守護</span>
          <span className="gd-dim">只用道具，不會移動角色；你自己玩的時候也照常運作</span>
        </div>
        <span className="gd-chip">{status.hook_cmd ? 'hook 指令通道已就緒' : '沒有 hook 指令通道'}</span>
        <span className="gd-chip">本次喝水 {status.drinks} 次</span>
        <button
          type="button" role="switch" aria-checked={running} aria-label="啟用常駐守護"
          className="gd-switch" onClick={toggle} disabled={busy || (!running && !status.hook_cmd)}
        />
      </section>
      {(notice || (running && status.problem)) && (
        <div className="gd-notice" role="status">{notice ?? status.problem}</div>
      )}
      {!status.hook_cmd && !running && (
        <div className="gd-notice">守護要透過 hook 送出使用道具的指令；這個遊戲視窗目前沒有 hook。設定仍可先編輯、儲存。</div>
      )}

      <section className="gd-panel">
        <header className="gd-head">
          <h3>補水</h3>
          <span className="gd-dim">只喝白名單裡的藥，從上往下用第一個身上有的；白名單是空的就不喝</span>
        </header>
        <div className="gd-wl-grid">
          {(['hp', 'mp'] as Resource[]).map(res => (
            <Whitelist
              key={res}
              res={res}
              pct={res === 'hp' ? rule.hp_pct : rule.mp_pct}
              vitals={status.vitals ?? null}
              items={res === 'hp' ? rule.hp_items : rule.mp_items}
              byId={byId}
              candidates={potions.filter(p => p.restores === res || p.restores === 'both')}
              onPct={v => update(res === 'hp' ? { ...rule, hp_pct: v } : { ...rule, mp_pct: v })}
              onItems={v => update(res === 'hp' ? { ...rule, hp_items: v } : { ...rule, mp_items: v })}
            />
          ))}
        </div>
        <div className="gd-fixed">
          <span>血量一變動就判斷，不等上一口生效</span>
          <span>掉得越多一次喝越多口（每次最多 4 口）</span>
          <span>背包數量只用來記錄，不會擋住下一口</span>
          <span>用完的藥留在清單上，補貨後照原順序</span>
        </div>
      </section>

      <section className="gd-panel">
        <header className="gd-head"><h3>守護紀錄</h3><span className="gd-dim">最新的在上面</span></header>
        {status.log.length === 0 ? (
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

function Whitelist({ res, pct, vitals, items, byId, candidates, onPct, onItems }: {
  res: Resource;
  pct: number;
  vitals: GuardVitals | null;
  items: number[];
  byId: Map<number, PotionCandidate>;
  candidates: PotionCandidate[];
  onPct: (v: number) => void;
  onItems: (v: number[]) => void;
}) {
  const [picking, setPicking] = useState(false);
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= items.length) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    onItems(next);
  };
  const addable = candidates.filter(c => !items.includes(c.item_id));
  const label = RES_LABEL[res];

  return (
    <div className="gd-wl">
      <Threshold res={res} pct={pct} vitals={vitals} onPct={onPct} />
      {items.length > 0 && (
        <ol className="gd-list">
          {items.map((id, i) => {
            const p = byId.get(id);
            const held = (p?.bag ?? 0) > 0;
            return (
              <li key={id} className={held ? '' : 'is-out'}>
                <span className="gd-n">{i + 1}.</span>
                <span className="gd-name">{p?.name ?? `#${id}`}</span>
                <span className="gd-cnt">{p ? `身上 ${p.bag} · 寵物 ${p.pet}` : '身上沒有'}</span>
                <span className="gd-ops">
                  <button type="button" aria-label={`${p?.name ?? id} 往上`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                  <button type="button" aria-label={`${p?.name ?? id} 往下`} disabled={i === items.length - 1} onClick={() => move(i, 1)}>↓</button>
                  <button type="button" aria-label={`移除 ${p?.name ?? id}`} onClick={() => onItems(items.filter(x => x !== id))}>×</button>
                </span>
              </li>
            );
          })}
        </ol>
      )}
      {picking ? (
        <div className="gd-picker" role="listbox" aria-label={`加入${label}藥`}>
          <div className="gd-picker-head">
            <span>身上和寵物背包裡會回{label}的藥</span>
            <button type="button" onClick={() => setPicking(false)}>關閉</button>
          </div>
          {addable.length === 0 ? (
            <div className="gd-dim gd-pad">沒有可以加入的藥。</div>
          ) : (
            <ul>
              {addable.map(c => (
                <li key={c.item_id}>
                  <button type="button" role="option" aria-selected={false} onClick={() => { onItems([...items, c.item_id]); }}>
                    <span>{c.name}{c.restores === 'both' && <span className="gd-dim">　體力＋真氣</span>}</span>
                    <span className="gd-cnt">身上 {c.bag}</span>
                    <span className="gd-cnt">寵物 {c.pet}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <button type="button" className="gd-add" onClick={() => setPicking(true)}>＋ 加入{label}藥</button>
      )}
    </div>
  );
}

const fmt = (n: number) => n.toLocaleString('en-US');

// The threshold as a slider: filled up to the threshold (where the guard
// drinks), grey after it; a thin marker shows where the character is now.
function Threshold({ res, pct, vitals, onPct }: {
  res: Resource;
  pct: number;
  vitals: GuardVitals | null;
  onPct: (v: number) => void;
}) {
  const label = RES_LABEL[res];
  const cur = vitals ? (res === 'hp' ? vitals.hp : vitals.mp) : null;
  const max = vitals ? (res === 'hp' ? vitals.hp_max : vitals.mp_max) : null;
  const now = cur !== null && max ? Math.max(0, Math.min(100, (cur * 100) / max)) : null;
  const id = `gd-${res}-pct`;
  return (
    <div className="gd-th">
      <div className="gd-th-head">
        <label htmlFor={id}>{label}低於 <b className="gd-th-v">{pct}%</b> 時喝</label>
        <span className="gd-cnt">
          {cur !== null && max ? `現在 ${fmt(cur)} / ${fmt(max)}（${Math.floor(now ?? 0)}%）` : '讀不到目前數值'}
        </span>
      </div>
      <div className={`gd-th-bar is-${res}`} style={{ ['--th' as string]: `${pct}%`, ['--now' as string]: `${now ?? 0}%` }}>
        <i className="gd-th-zone" />
        {now !== null && <i className="gd-th-now" title={`現在 ${Math.floor(now)}%`} />}
        <input
          id={id} type="range" min={1} max={99} step={1} value={pct}
          aria-valuetext={`${label}低於 ${pct}% 時喝`}
          onChange={e => onPct(Number(e.target.value))}
        />
      </div>
    </div>
  );
}
