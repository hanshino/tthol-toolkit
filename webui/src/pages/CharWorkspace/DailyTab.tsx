import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type { AttackSkillCandidate, CombatRule, DailyQueueItem, DailyView, GuardStartResult, TowerAttackReach, TowerConfig, TowerEstimate, TowerStatus, TowerView } from '../../api/types';
import { reportClientError } from '../../diag/report';
import './guard.css';
import './daily.css';

// 日常: modules that move the character (services/tower_run.py). The guard
// runs alongside and keeps potions and buffs up; a module only walks, fights
// and talks.
const POLL_MS = 1000;
const SAVE_DELAY_MS = 400;
// missions 18913-18919 (神武玄天塔-辰星關 ...): sestage 1704-1710 in order.
const STAGES = ['辰星關', '太白關', '熒惑關', '歲星關', '鎮星關', '冽星關', '颶星關'];
// 狐光靈珠 skips at 燕飄風 (services/tower.py SKIP_LEVEL): 辰星 ... 冽星.
const SKIPS = [80, 100, 120, 140, 160, 180].map((level, i) => ({ name: STAGES[i], level }));
const PHASE_LABEL: Record<TowerStatus['log'][number]['phase'], string> = {
  sent: '送出', confirmed: '完成', unconfirmed: '未確認', error: '錯誤', info: '',
};

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
const mmss = (secs: number) => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`;
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

type Settings = { combat: CombatRule; config: TowerConfig };

export function DailyTab({ pid, active }: { pid: number; active: boolean }) {
  const [view, setView] = useState<TowerView | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [estimateText, setEstimateText] = useState<string | null>(null);
  const [estimateWarn, setEstimateWarn] = useState(false);
  const [estimateAttacks, setEstimateAttacks] = useState<TowerAttackReach[]>([]);
  const dirty = useRef(false);
  const saveTimer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const v = await get<TowerView>(`/api/characters/${pid}/tower`);
      setView(v);
      if (!dirty.current) setSettings({ combat: v.combat, config: v.config });
    } catch (e) {
      reportClientError(e, { component: 'DailyTab.refresh', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    refresh();
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [active, refresh]);

  const update = (next: Settings) => {
    setSettings(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/tower/settings`, next);
        setNotice(null);
      } catch (e) {
        setNotice('設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'DailyTab.save', silent: true });
      } finally {
        dirty.current = false;
      }
    }, SAVE_DELAY_MS);
  };

  const toggle = async () => {
    if (!view) return;
    setBusy(true);
    try {
      if (view.status.running) {
        await post(`/api/characters/${pid}/tower/stop`);
      } else {
        const r = await post<GuardStartResult>(`/api/characters/${pid}/tower/start`);
        setNotice(r.ok ? null : r.reason ?? '無法開始');
      }
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'DailyTab.toggle' });
    } finally {
      setBusy(false);
    }
  };

  const tidyNow = async () => {
    setBusy(true);
    try {
      const r = await post<GuardStartResult>(`/api/characters/${pid}/tower/tidy`);
      setNotice(r.ok ? null : r.reason ?? '無法開始整理');
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'DailyTab.tidy' });
    } finally {
      setBusy(false);
    }
  };

  const estimate = async () => {
    setEstimating(true);
    try {
      const r = await post<TowerEstimate>(`/api/characters/${pid}/tower/estimate`);
      if (!r.ok) {
        setEstimateText(r.reason ?? '估算不出來');
        setEstimateWarn(true);
        setEstimateAttacks([]);
        return;
      }
      setEstimateAttacks(r.attacks ?? []);
      const why = r.blocker ? `（${r.blocker}）` : '';
      const buffs = r.missing_buffs == null
        ? '　讀不到 buff 清單，無法確認 buff 是否上滿。'
        : r.missing_buffs.length
          ? `　還沒上的 buff：${r.missing_buffs.join('、')}，上滿後命中可能更高，建議補完再估一次。`
          : '';
      // The best hitting attack carries the climb; with none set, the bare hit.
      const best = r.attacks?.[0];
      const by = best ? `用${best.name}（命中 ${r.hit} × ${Math.round(best.rate * 100)}% = ${best.hit}）` : `依目前命中 ${r.hit}`;
      setEstimateText(
        r.max_floor > 0
          ? `${by}（LV${r.level}），預估可打到第 ${r.max_floor} 層${why}${r.applied ? '，已設為停止層。' : '。'}${buffs}`
          : `${by}（LV${r.level}），第 1 層就可能打不贏${why}。${buffs}`,
      );
      setEstimateWarn(!!r.missing_buffs?.length || r.max_floor === 0);
      dirty.current = false;
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'DailyTab.estimate' });
    } finally {
      setEstimating(false);
    }
  };

  if (!view || !settings) return <section className="gd-panel gd-dim">讀取日常課題…</section>;
  const st = view.status;
  const running = st.running;
  const today_ = view.record.date === today() ? view.record : null;
  // done: false = stopped mid-climb (error, potions); the tower picks up from there.
  const doneToday = !!today_ && (today_.done ?? today_.top_floor > 0);
  const resumable = !!today_ && !doneToday && today_.top_floor > 0;

  return (
    <div className="gd">
      <QueueSection pid={pid} active={active} />

      <section className="gd-panel gd-strip" aria-label="日常課題執行">
        <span className={`gd-state${running ? ' is-on' : ''}${running && st.problem ? ' is-warn' : ''}`}>
          <i />{running ? (st.problem ? '等待中' : '執行中') : '已停止'}
        </span>
        <div className="gd-strip-text">
          <span className="gd-title">神武玄天塔</span>
          <span className="gd-dim">
            {running ? st.problem ?? st.step ?? '準備中' : '站在玄天之境或塔裡按開始；補水、buff 由常駐守護負責，會自動打開'}
          </span>
        </div>
        <button type="button" className={`dl-btn${running ? ' is-stop' : ''}`} onClick={toggle}
          disabled={busy || (!running && !view.hook_ready)}>
          {running ? '停止' : '開始登塔'}
        </button>
      </section>
      {notice && <div className="gd-notice" role="status">{notice}</div>}
      {!view.hook_ready && !running && (
        <div className="gd-notice">登塔要透過 hook 走路、打怪、對話；這個遊戲視窗的 hook 沒有連上，或缺少需要的指令。</div>
      )}

      <Progress st={st} />

      <section className="gd-panel">
        <header className="gd-head">
          <h3>今日</h3>
          <span className="gd-dim">遊戲裡讀不到每日次數，這裡只記工具看到的登塔</span>
        </header>
        <div className="dl-today">
          {doneToday
            ? <span>今天已挑戰，最高打到 <b className="dl-num">第 {view.record.top_floor} 層</b></span>
            : resumable
              ? <span>今天打到 <b className="dl-num">第 {view.record.top_floor} 層</b>，還沒打完，再按開始會接續</span>
              : <span className="gd-dim">今天還沒有用工具登塔</span>}
          {view.record.ended && <span className="gd-dim">上次結束：{view.record.ended}</span>}
        </div>
      </section>

      <CombatSection
        combat={settings.combat}
        skills={view.skills}
        onChange={combat => update({ ...settings, combat })}
      />

      <section className="gd-panel">
        <header className="gd-head"><h3>登塔設定</h3></header>
        <div className="dl-row">
          <label htmlFor="dl-stop">打到第</label>
          <input id="dl-stop" type="number" min={1} max={100} placeholder="—"
            value={settings.config.stop_floor ?? ''}
            onChange={e => {
              const v = e.target.value === '' ? null : Math.min(100, Math.max(1, Math.floor(Number(e.target.value)) || 1));
              update({ ...settings, config: { ...settings.config, stop_floor: v } });
            }} />
          <span>層就離開塔（空白＝一路打到被送出塔）</span>
          <button type="button" className="dl-btn dl-btn-sm" onClick={estimate} disabled={estimating}>
            依目前命中估算
          </button>
        </div>
        {estimateText && (
          <div className={`dl-estimate${estimateWarn ? ' is-warn' : ''}`} role="status">
            {estimateText}
            {estimateAttacks.length > 1 && (
              <ul className="dl-attacks">
                {estimateAttacks.map(a => (
                  <li key={a.name}>
                    <span>{a.name}</span>
                    <span className="dl-dim">命中 ×{Math.round(a.rate * 100)}% = {a.hit}</span>
                    <span>{a.max_floor > 0 ? `到第 ${a.max_floor} 層` : '第 1 層就打不中'}</span>
                    {a.blocker && <span className="dl-dim">{a.blocker}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="dl-row dl-skip">
          <label htmlFor="dl-skip">狐光靈珠</label>
          <select id="dl-skip" className="dl-select" value={settings.config.skip_to ?? ''}
            onChange={e => update({
              ...settings,
              config: { ...settings.config, skip_to: e.target.value ? Number(e.target.value) : null },
            })}>
            <option value="">不使用</option>
            {SKIPS.map((s, i) => (
              <option key={s.name} value={i + 1}>略過到{s.name}（第 {i * 10 + 10} 層，LV{s.level}）</option>
            ))}
          </select>
          <span>每天第一次進塔前，依序一關一關略過；等級或靈珠不夠就略過到能略過的那關</span>
        </div>
        <PotionFloors config={settings.config} onChange={config => update({ ...settings, config })} />
        <BoxTidy config={settings.config} onChange={config => update({ ...settings, config })}
          busy={busy || view.status.running} onTidy={tidyNow} />
        <div className="gd-fixed">
          <span>層數是全塔編號：辰星關 1–10、太白關 11–20…</span>
          <span>藥量＝補水白名單的藥，背包加寵物背包</span>
          <span>估算：命中要 ≥ 該房怪物最高迴避（依 10/05 實測校正），也看等級門檻</span>
          <span>出口對話依選項的 jump id 選，不靠位置</span>
          <span>死亡、背包滿或超重、等級不夠就停</span>
          <span>狐光靈珠：每關 1 顆（冽星關 2 顆），要 1 格背包空位、負重剩 5；略過的關給該關寶箱與經驗；颶星關不能略過</span>
        </div>
      </section>

      <section className="gd-panel">
        <header className="gd-head"><h3>執行紀錄</h3><span className="gd-dim">最新的在上面</span></header>
        {st.log.length === 0 ? (
          <div className="gd-dim">還沒有紀錄。</div>
        ) : (
          <ul className="gd-log">
            {st.log.map(e => (
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

// The current 關 as ten floor cells, plus the room's kills and timer.
function Progress({ st }: { st: TowerStatus }) {
  const now = Date.now();  // the tab polls every second, which re-renders this
  if (st.stage_id == null || st.floor == null || st.room == null) {
    if (!st.floors.length) return null;
  }
  const base = st.floor != null && st.room != null ? st.floor - st.room : (st.floors.at(-1)?.floor ?? 1) - 1;
  const index = Math.floor(base / 10);
  const cleared = new Map(st.floors.map(f => [f.floor, f]));
  const roomSecs = st.room_started != null ? Math.max(0, now / 1000 - st.room_started) : null;
  return (
    <section className="gd-panel">
      <header className="gd-head">
        <h3>{STAGES[index] ?? st.stage_name ?? '玄天塔'}</h3>
        <span className="gd-dim">第 {base + 1}–{base + 10} 層</span>
      </header>
      <ol className="dl-ladder" aria-label="這一關的層數進度">
        {Array.from({ length: 10 }, (_, i) => {
          const floor = base + 1 + i;
          const f = cleared.get(floor);
          const cur = st.running && floor === st.floor;
          const note = f?.skipped ? '略過' : f ? mmss(f.secs) : cur ? '進行中' : '';
          return (
            <li key={floor} className={f?.skipped ? 'is-skip' : f ? 'is-done' : cur ? 'is-cur' : ''}
              aria-label={`第 ${floor} 層${f?.skipped ? '，用狐光靈珠略過' : f ? `，${mmss(f.secs)} 通過` : cur ? '，進行中' : ''}`}>
              <b>{floor}</b>
              <span>{note}</span>
            </li>
          );
        })}
      </ol>
      {st.running && st.room != null && (
        <div className="dl-room">
          <div><span className="dl-lbl">擊倒</span><b className="dl-num">{st.kills} / {st.expect}</b></div>
          <div><span className="dl-lbl">這一房</span><b className="dl-num">{roomSecs != null ? mmss(roomSecs) : '—'}</b></div>
          <div><span className="dl-lbl">本次通過</span><b className="dl-num">{st.floors.length} 層</b></div>
        </div>
      )}
    </section>
  );
}

// Like the battle puppet's 戰鬥 page: basic attack, one opener, three rotation skills.
function CombatSection({ combat, skills, onChange }: {
  combat: CombatRule;
  skills: AttackSkillCandidate[];
  onChange: (c: CombatRule) => void;
}) {
  const rotation = [0, 1, 2].map(i => combat.rotation?.[i] ?? null);
  const setSlot = (i: number, v: number | null) => {
    const next = [...rotation];
    next[i] = v;
    onChange({ ...combat, rotation: next.filter((x): x is number => x != null) });
  };
  const options = (
    <>
      <option value="">（不用）</option>
      {skills.map(s => (
        <option key={s.magic_id} value={s.magic_id}>
          {s.name} Lv{s.level}・真氣 {s.mp}{s.area ? '・範圍' : ''}
        </option>
      ))}
    </>
  );
  return (
    <section className="gd-panel">
      <header className="gd-head">
        <h3>戰鬥</h3>
        <span className="gd-dim">照戰鬥木偶的設定：換目標先放首次攻擊，之後常用技能 1 → 2 → 3 輪流；真氣不夠的格子跳過</span>
      </header>
      <div className="dl-combat">
        <label className="dl-check">
          <input type="checkbox" checked={combat.basic ?? false} onChange={e => onChange({ ...combat, basic: e.target.checked })} />
          使用普通攻擊（和技能一起打，不是備用）
        </label>
        <label className="dl-field">
          <span>首次攻擊</span>
          <select value={combat.opener ?? ''} onChange={e => onChange({ ...combat, opener: e.target.value ? Number(e.target.value) : null })}>
            {options}
          </select>
        </label>
        {rotation.map((v, i) => (
          <label key={i} className="dl-field">
            <span>常用技能 {i + 1}</span>
            <select value={v ?? ''} onChange={e => setSlot(i, e.target.value ? Number(e.target.value) : null)}>
              {options}
            </select>
          </label>
        ))}
      </div>
      {skills.length === 0 && <div className="gd-dim">讀不到角色學會的攻擊技能。</div>}
      <div className="dl-combat dl-pick">
        <label className="dl-field">
          <span>選新目標</span>
          <select value={combat.target ?? 'nearest'}
            onChange={e => onChange({ ...combat, target: e.target.value as CombatRule['target'] })}>
            <option value="nearest">最近的先打</option>
            <option value="weakest">小怪先打（菁英最後）</option>
          </select>
        </label>
        <label className="dl-check dl-check-inline">
          <input type="checkbox" checked={combat.avoid_packs ?? false}
            onChange={e => onChange({ ...combat, avoid_packs: e.target.checked })} />
          避開怪群：先打周圍怪少的
        </label>
      </div>
      <div className="gd-fixed">
        <span>鎖定的怪沒死就一直打它，死了才換下一隻</span>
        <span>首次攻擊要看到命中才換常用技能</span>
        <span>2.2 秒沒打中就重新下攻擊指令</span>
        <span>下一招等上一招的冷卻＋硬直（至少 0.45 秒）</span>
        <span>守護補 buff 後停 1 秒</span>
      </div>
    </section>
  );
}

// Potion floors: leave at a floor's exit when short; log out mid-floor when out.
function PotionFloors({ config, onChange }: { config: TowerConfig; onChange: (c: TowerConfig) => void }) {
  const num = (v: string, min: number): number | null =>
    v === '' ? null : Math.min(100000, Math.max(min, Math.floor(Number(v)) || min));
  return (
    <div className="dl-floors">
      <div className="dl-row">
        <span>過層時體力藥少於</span>
        <input type="number" min={1} placeholder="—" aria-label="接關門檻：體力藥"
          value={config.leave_hp_below ?? ''}
          onChange={e => onChange({ ...config, leave_hp_below: num(e.target.value, 1) })} />
        <span>或真氣藥少於</span>
        <input type="number" min={1} placeholder="—" aria-label="接關門檻：真氣藥"
          value={config.leave_mp_below ?? ''}
          onChange={e => onChange({ ...config, leave_mp_below: num(e.target.value, 1) })} />
        <span>個，就在出口選離開（空白＝不檢查）</span>
      </div>
      <div className="dl-row">
        <label className="dl-check dl-check-inline">
          <input type="checkbox" checked={config.logout ?? false}
            onChange={e => onChange({ ...config, logout: e.target.checked })} />
          打到一半藥剩
        </label>
        <span>體力藥 ≤</span>
        <input type="number" min={0} disabled={!config.logout} aria-label="登出門檻：體力藥"
          value={config.logout_hp_at ?? 0}
          onChange={e => onChange({ ...config, logout_hp_at: num(e.target.value, 0) ?? 0 })} />
        <span>或真氣藥 ≤</span>
        <input type="number" min={0} placeholder="—" disabled={!config.logout} aria-label="登出門檻：真氣藥"
          value={config.logout_mp_at ?? ''}
          onChange={e => onChange({ ...config, logout_mp_at: num(e.target.value, 0) })} />
        <span>個就登出遊戲（Esc → 登出遊戲）</span>
      </div>
    </div>
  );
}

// 寶箱整理 after a run that ended the normal way (services/box_tidy.py).
function BoxTidy({ config, onChange, busy, onTidy }: {
  config: TowerConfig; onChange: (c: TowerConfig) => void; busy: boolean; onTidy: () => void;
}) {
  const on = config.tidy_boxes ?? false;
  return (
    <div className="dl-floors">
      <div className="dl-row">
        <label className="dl-check dl-check-inline">
          <input type="checkbox" checked={on}
            onChange={e => onChange({ ...config, tidy_boxes: e.target.checked })} />
          跑完自動整理寶箱
        </label>
        <span>：開完各關寶箱，寶箱開出的藥每種留</span>
        <input type="number" min={0} disabled={!on} aria-label="寶箱藥品每種保留"
          value={config.keep_potions ?? 50}
          onChange={e => onChange({
            ...config,
            keep_potions: Math.min(10000, Math.max(0, Math.floor(Number(e.target.value)) || 0)),
          })} />
        <span>個，多的吃掉；蒐藏冊沒收過的神兵先蒐藏，其餘神兵、技能書、覺醒符存倉</span>
        <button type="button" className="dl-btn dl-btn-sm" disabled={busy} onClick={onTidy}
          title="不登塔，現在就照這個設定整理背包裡的關寶箱">現在整理寶箱</button>
      </div>
    </div>
  );
}

const ITEM_NOTE: Record<DailyQueueItem['state'], string> = {
  pending: '', moving: '前往中', running: '進行中', done: '完成', skipped: '今日已做', error: '停下', halted: '停住',
};

// This character's 日常 list: run order, add / remove, and running it in one go
// (the 總覽 page starts several characters' lists at once).
function QueueSection({ pid, active }: { pid: number; active: boolean }) {
  const [view, setView] = useState<DailyView | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState('');

  const refresh = useCallback(async () => {
    try {
      setView(await get<DailyView>(`/api/characters/${pid}/daily`));
    } catch (e) {
      reportClientError(e, { component: 'DailyTab.queue.refresh', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    refresh();
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [active, refresh]);

  if (!view) return null;
  const keys = view.config.modules;
  const running = view.status.running;
  const titles = new Map(view.modules.map(m => [m.key, m.title]));
  const items = view.status.items;
  const left = view.modules.filter(m => !keys.includes(m.key));

  const save = async (next: string[]) => {
    setView({ ...view, config: { modules: next } });
    try {
      await put(`/api/characters/${pid}/daily/queue`, { modules: next });
      setNotice(null);
      await refresh();
    } catch (e) {
      setNotice('清單沒有存到：角色可能還沒定位');
      reportClientError(e, { component: 'DailyTab.queue.save', silent: true });
    }
  };
  const move = (i: number, d: -1 | 1) => {
    const next = [...keys];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    save(next);
  };
  const toggle = async () => {
    setBusy(true);
    try {
      if (running) {
        await post(`/api/characters/${pid}/daily/stop`);
      } else {
        const r = await post<GuardStartResult>(`/api/characters/${pid}/daily/start`);
        setNotice(r.ok ? null : r.reason ?? '無法開始');
      }
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'DailyTab.queue.toggle' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="gd-panel" aria-label="日常清單">
      <header className="gd-head">
        <h3>日常清單</h3>
        <span className="gd-dim">照順序一項一項跑；今天做過的略過，一項出錯就停住整串，再按開始從停下的那項接著跑</span>
      </header>
      <ol className="dl-queue">
        {keys.map((k, i) => {
          const item = items.find(it => it.module === k);
          return (
            <li key={k} data-s={item?.state ?? 'pending'}>
              <span className="dl-queue-n">{i + 1}</span>
              <span className="dl-queue-t">{titles.get(k) ?? k}</span>
              <span className="dl-queue-s">
                {item ? (item.state === 'done' && item.result ? `完成 · ${item.result}` : ITEM_NOTE[item.state]) : ''}
              </span>
              <span className="dl-queue-a">
                <button type="button" className="dl-btn dl-btn-sm" disabled={running || i === 0}
                  onClick={() => move(i, -1)} aria-label={`${titles.get(k)} 往前`}>↑</button>
                <button type="button" className="dl-btn dl-btn-sm" disabled={running || i === keys.length - 1}
                  onClick={() => move(i, 1)} aria-label={`${titles.get(k)} 往後`}>↓</button>
                <button type="button" className="dl-btn dl-btn-sm" disabled={running}
                  onClick={() => save(keys.filter(x => x !== k))} aria-label={`從清單移除 ${titles.get(k)}`}>移除</button>
              </span>
            </li>
          );
        })}
        {keys.length === 0 && <li className="gd-dim">清單是空的，從下面加入要跑的項目。</li>}
      </ol>
      <div className="dl-row" style={{ marginTop: 10 }}>
        {left.length > 0 && (
          <>
            <label htmlFor="dl-add">加入</label>
            <select id="dl-add" className="dl-select" value={adding} disabled={running}
              onChange={e => setAdding(e.target.value)}>
              <option value="">選一個日常…</option>
              {left.map(m => <option key={m.key} value={m.key}>{m.title}</option>)}
            </select>
            <button type="button" className="dl-btn dl-btn-sm" disabled={running || !adding}
              onClick={() => { save([...keys, adding]); setAdding(''); }}>加到最後</button>
          </>
        )}
        <button type="button" className={`dl-btn${running ? ' is-stop' : ''}`} style={{ marginLeft: 'auto' }}
          onClick={toggle} disabled={busy || (!running && keys.length === 0)}>
          {running ? '停止清單' : '開始清單'}
        </button>
      </div>
      {notice && <div className="gd-notice" role="status" style={{ marginTop: 8 }}>{notice}</div>}
    </section>
  );
}
