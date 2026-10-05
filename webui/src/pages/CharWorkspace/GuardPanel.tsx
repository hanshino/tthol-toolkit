import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type { BuffSkillCandidate, CureCandidate, GuardConfig, GuardLogEntry, GuardStatus, GuardVitals, PotionCandidate } from '../../api/types';
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
type Cfg = { potion: Rule; cure: { items: number[] }; buff: { skills: number[] } };

const toCfg = (c?: GuardConfig): Cfg => ({
  potion: {
    hp_pct: c?.potion.hp_pct ?? 70,
    mp_pct: c?.potion.mp_pct ?? 30,
    hp_items: c?.potion.hp_items ?? [],
    mp_items: c?.potion.mp_items ?? [],
  },
  cure: { items: c?.cure?.items ?? [] },
  buff: { skills: c?.buff?.skills ?? [] },
});

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });

export function GuardPanel({ pid, active }: { pid: number; active: boolean }) {
  const [status, setStatus] = useState<GuardStatus | null>(null);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [potions, setPotions] = useState<PotionCandidate[]>([]);
  const [cures, setCures] = useState<CureCandidate[]>([]);
  const [skills, setSkills] = useState<BuffSkillCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const saveTimer = useRef<number | null>(null);
  const dirty = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const s = await get<GuardStatus>(`/api/characters/${pid}/guard`);
      setStatus(s);
      // Do not overwrite an edit that is waiting to be saved.
      if (!dirty.current) setCfg(toCfg(s.config));
    } catch (e) {
      reportClientError(e, { component: 'GuardPanel.refresh', silent: true });
    }
  }, [pid]);

  const loadPotions = useCallback(async () => {
    try {
      const [p, c, k] = await Promise.all([
        get<PotionCandidate[]>(`/api/characters/${pid}/guard/potions`),
        get<CureCandidate[]>(`/api/characters/${pid}/guard/cures`),
        get<BuffSkillCandidate[]>(`/api/characters/${pid}/guard/skills`),
      ]);
      setPotions(p);
      setCures(c);
      setSkills(k);
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

  const update = (next: Cfg) => {
    setCfg(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/guard/config`, next);
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

  if (!status || !cfg) return <section className="gd-panel gd-dim">讀取守護設定…</section>;

  const rule = cfg.potion;
  const setRule = (potion: Rule) => update({ ...cfg, potion });

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
        <span className="gd-chip">解狀態 {status.cures} 次</span>
        <span className="gd-chip">補 buff {status.casts} 次</span>
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
              onPct={v => setRule(res === 'hp' ? { ...rule, hp_pct: v } : { ...rule, mp_pct: v })}
              onItems={v => setRule(res === 'hp' ? { ...rule, hp_items: v } : { ...rule, mp_items: v })}
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

      <CureSection
        items={cfg.cure.items}
        candidates={cures}
        debuffs={running ? status.debuffs : null}
        onItems={items => update({ ...cfg, cure: { items } })}
      />

      <BuffSection
        skills={cfg.buff.skills}
        candidates={skills}
        onSkills={next => update({ ...cfg, buff: { skills: next } })}
      />

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

// 解狀態: tick the cure items to use as soon as the debuff each one clears
// shows up. Only items the character holds are listed, plus ticked ones that
// ran out (so they can still be unticked).
function CureSection({ items, candidates, debuffs, onItems }: {
  items: number[];
  candidates: CureCandidate[];
  debuffs: string[] | null;
  onItems: (v: number[]) => void;
}) {
  const held = new Set(candidates.map(c => c.item_id));
  const gone = items.filter(id => !held.has(id));
  const toggle = (id: number, on: boolean) =>
    onItems(on ? [...items, id] : items.filter(x => x !== id));

  return (
    <section className="gd-panel">
      <header className="gd-head">
        <h3>解狀態</h3>
        <span className="gd-dim">中了勾選的解藥能解的狀態就用；沒勾的道具不會動</span>
        {debuffs !== null && (
          <span className="gd-cure-now">
            目前狀態：{debuffs.length ? debuffs.map(d => <b key={d}>{d}</b>) : <span className="gd-dim">無</span>}
          </span>
        )}
      </header>
      {candidates.length === 0 && gone.length === 0 ? (
        <div className="gd-dim">身上和寵物背包裡沒有解藥（明目散、解毒劑、活竅散…）。</div>
      ) : (
        <ul className="gd-cures">
          {candidates.map(c => {
            const on = items.includes(c.item_id);
            const id = `gd-cure-${c.item_id}`;
            return (
              <li key={c.item_id} className={c.bag > 0 ? '' : 'is-out'}>
                <input id={id} type="checkbox" checked={on} onChange={e => toggle(c.item_id, e.target.checked)} />
                <label htmlFor={id}>
                  <span className="gd-name">{c.name}</span>
                  <span className="gd-cure-st">解{c.status}</span>
                  <span className="gd-cnt">身上 {c.bag} · 寵物 {c.pet}</span>
                </label>
              </li>
            );
          })}
          {gone.map(itemId => {
            const id = `gd-cure-${itemId}`;
            return (
              <li key={itemId} className="is-out">
                <input id={id} type="checkbox" checked onChange={() => toggle(itemId, false)} />
                <label htmlFor={id}>
                  <span className="gd-name">#{itemId}</span>
                  <span className="gd-cure-st" />
                  <span className="gd-cnt">身上沒有</span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      <div className="gd-fixed">
        <span>只解解藥對應的那一種狀態</span>
        <span>用了 2 秒內沒解就再用，最多 3 次</span>
        <span>補體力優先，接著解狀態，再補真氣</span>
      </div>
    </section>
  );
}

const TARGET_LABEL: Record<BuffSkillCandidate['target'], string> = { self: '自身', ally: '單體', group: '群體' };

// buff 維持: tick the learned buff skills to keep up. A ticked skill is cast
// on the character when its buff is gone or about to end.
function BuffSection({ skills, candidates, onSkills }: {
  skills: number[];
  candidates: BuffSkillCandidate[];
  onSkills: (v: number[]) => void;
}) {
  const now = Date.now() / 1000;
  const toggle = (id: number, on: boolean) =>
    onSkills(on ? [...skills, id] : skills.filter(x => x !== id));
  // The same status group from two skills overwrites itself: warn about it.
  const groups = new Map<number, number>();
  for (const c of candidates) if (skills.includes(c.magic_id)) groups.set(c.group, (groups.get(c.group) ?? 0) + 1);

  return (
    <section className="gd-panel">
      <header className="gd-head">
        <h3>buff 維持</h3>
        <span className="gd-dim">勾選的技能 buff 消失或剩不到 3 秒就對自己重放；真氣不夠、死亡時不放</span>
      </header>
      {candidates.length === 0 ? (
        <div className="gd-dim">讀不到角色的技能，或沒有可以對自己施放的 buff 技能。</div>
      ) : (
        <ul className="gd-cures">
          {candidates.map(c => {
            const on = skills.includes(c.magic_id);
            const id = `gd-buff-${c.magic_id}`;
            const left = c.expires_at != null ? Math.max(0, Math.round(c.expires_at - now)) : null;
            const clash = on && (groups.get(c.group) ?? 0) > 1;
            return (
              <li key={c.magic_id}>
                <input id={id} type="checkbox" checked={on} onChange={e => toggle(c.magic_id, e.target.checked)} />
                <label htmlFor={id} title={`${c.status}・${TARGET_LABEL[c.target]}・真氣 ${c.mp}・持續 ${Math.round(c.duration_s / 60)} 分`}>
                  <span className="gd-name">
                    {c.name} <span className="gd-dim">Lv{c.level}</span>
                    {clash && <span className="gd-buff-clash">與其他勾選技能同類，會互相覆蓋</span>}
                  </span>
                  <span className={`gd-cure-st${c.active ? ' gd-buff-on' : ''}`}>
                    {c.active ? (left != null ? `生效中 ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : '生效中') : '未生效'}
                  </span>
                  <span className="gd-cnt">真氣 {c.mp}</span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      <div className="gd-fixed">
        <span>每次施放至少間隔 2 秒</span>
        <span>放了 4 次都沒生效就暫停該技能 60 秒</span>
        <span>換地圖清掉的 buff 會自動補回</span>
        <span>需要 hook 回報 buff 清單</span>
      </div>
    </section>
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
