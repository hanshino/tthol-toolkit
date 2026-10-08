import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type { GrindSettings, GrindStatus, GrindView, GuardStartResult } from '../../api/types';
import { reportClientError } from '../../diag/report';
import { CombatSection } from './CombatSection';
import { grindState, stateClass } from './assistState';
import './handoff.css';
import './supply.css';
import './grind.css';

// 打怪 (services/grind.py): fight the monsters around the spot the character
// stands on, on any map, until stopped. How it fights is the character's one
// combat setting (shared with 日常); the guard keeps potions and buffs up.
const VIEW_MS = 5000;
const STATUS_MS = 1000;
const SAVE_DELAY_MS = 400;

const PHASE_LABEL: Record<GrindStatus['log'][number]['phase'], string> = {
  sent: '送出', confirmed: '完成', unconfirmed: '未確認', error: '錯誤', info: '',
};

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
const mmss = (secs: number) => {
  const m = Math.floor(secs / 60);
  return `${m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}` : m}:${String(Math.floor(secs % 60)).padStart(2, '0')}`;
};

export function GrindPanel({ pid, active }: { pid: number; active: boolean }) {
  const [view, setView] = useState<GrindView | null>(null);
  const [status, setStatus] = useState<GrindStatus | null>(null);
  const [settings, setSettings] = useState<GrindSettings | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);
  const dirty = useRef(false);

  const loadView = useCallback(async () => {
    try {
      const v = await get<GrindView>(`/api/characters/${pid}/grind`);
      setView(v);
      setStatus(v.status);
      if (!dirty.current) setSettings({ combat: v.combat, config: v.config });
    } catch (e) {
      reportClientError(e, { component: 'GrindPanel.view', silent: true });
    }
  }, [pid]);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await get<GrindStatus>(`/api/characters/${pid}/grind/status`));
    } catch (e) {
      reportClientError(e, { component: 'GrindPanel.status', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    loadView();
    const v = window.setInterval(loadView, VIEW_MS);
    const s = window.setInterval(loadStatus, STATUS_MS);
    return () => { window.clearInterval(v); window.clearInterval(s); };
  }, [active, loadView, loadStatus]);

  const update = (next: GrindSettings) => {
    setSettings(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`/api/characters/${pid}/grind/settings`, next);
        setNotice(null);
      } catch (e) {
        setNotice('設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'GrindPanel.save', silent: true });
      } finally {
        dirty.current = false;
      }
    }, SAVE_DELAY_MS);
  };

  if (!view || !settings) return <section className="gd-panel gd-dim">讀取打怪設定…</section>;

  const running = !!status?.running;
  const state = grindState(status);
  const { combat, config } = settings;
  const noAttack = !combat.basic && !combat.opener && !(combat.rotation?.length);
  const only = new Set(config.only ?? []);

  const toggle = async () => {
    setBusy(true);
    try {
      if (running) {
        await post(`/api/characters/${pid}/grind/stop`);
      } else {
        const r = await post<GuardStartResult>(`/api/characters/${pid}/grind/start`);
        setNotice(r.ok ? null : r.reason ?? '無法開始');
      }
      await loadStatus();
    } catch (e) {
      reportClientError(e, { component: 'GrindPanel.toggle' });
    } finally {
      setBusy(false);
    }
  };

  const pick = (id: number) => {
    const next = only.has(id) ? (config.only ?? []).filter(x => x !== id) : [...(config.only ?? []), id];
    update({ ...settings, config: { ...config, only: next } });
  };
  const elapsed = status?.started && running ? Date.now() / 1000 - status.started : null;

  return (
    <div className="ho">
      <section className="gd-panel gd-strip" aria-label="打怪">
        <span className={stateClass(state)}><i />{state.text}</span>
        <div className="gd-strip-text">
          <span className="gd-title">打怪</span>
          <span className="gd-dim">站在哪就在哪打：打起點附近的怪，沒怪就走回起點等重生。死亡或換地圖就停</span>
        </div>
        <button type="button" className={running ? '' : 'is-primary'} onClick={toggle}
          disabled={busy || (!running && (!view.hook_ready || noAttack))}>
          {running ? '停止' : '開始打怪'}
        </button>
      </section>
      {notice && <div className="gd-notice" role="status">{notice}</div>}
      {status?.warning && running && <div className="gd-notice" role="status">{status.warning}</div>}
      {!view.hook_ready && <div className="gd-notice">這個視窗的 hook 沒有打怪要用的指令（status / near / walk / attack / cast）。</div>}
      {!running && noAttack && <div className="gd-notice">還沒設定攻擊方式：在下面「戰鬥」勾普攻，或選至少一個技能。</div>}
      {running && (status?.problem || status?.step) && <div className="sp-step" role="status">{status?.problem ?? status?.step}</div>}

      <section className="gd-panel ho-stats" aria-label="打怪狀態">
        <div>
          <span className="ho-lbl">擊殺</span>
          <span className="ho-num">{status?.kills ?? 0}</span>
        </div>
        <div>
          <span className="ho-lbl">目前目標</span>
          <span className="ho-val">{running && status?.target ? status.target : '—'}</span>
        </div>
        <div>
          <span className="ho-lbl">中心</span>
          <span className="ho-num">{status?.anchor ? `${status.anchor[0]}, ${status.anchor[1]}` : '開始時的位置'}</span>
        </div>
        <label className="ho-slots">
          <span className="ho-lbl">範圍（格）</span>
          <input type="number" min={2} max={40} value={config.radius ?? 10}
            onChange={e => update({ ...settings, config: { ...config, radius: Math.min(40, Math.max(2, Math.floor(Number(e.target.value)) || 2)) } })} />
        </label>
      </section>
      {elapsed != null && <div className="gd-dim gr-elapsed">已打 {mmss(elapsed)}</div>}

      <section className="gd-panel">
        <header className="gd-head">
          <h3>只打這些怪</h3>
          <span className="gd-dim">{only.size === 0 ? '沒選＝範圍內的怪都打' : `只打選了的 ${only.size} 種`}</span>
        </header>
        {view.monsters.length === 0 ? (
          <div className="gd-dim">畫面上沒有怪。走到怪旁邊，這裡就會列出來。</div>
        ) : (
          <div className="gr-chips" role="group" aria-label="怪物">
            {view.monsters.map(m => (
              <button key={m.npc_id} type="button" className="gr-chip" aria-pressed={only.has(m.npc_id)}
                onClick={() => pick(m.npc_id)}>
                <span>{m.name}</span>
                {m.level != null && <span className="gr-chip-lv">Lv{m.level}</span>}
                <span className="gr-chip-n">{m.count > 0 ? `×${m.count}` : '不在畫面'}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      <CombatSection combat={combat} skills={view.skills}
        onChange={c => update({ ...settings, combat: c })} />

      <section className="gd-panel">
        <header className="gd-head">
          <h3>打怪紀錄</h3>
          <span className="gd-dim">{!running && status?.ended ? `上次：${status.ended}` : '最新的在下面'}</span>
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
