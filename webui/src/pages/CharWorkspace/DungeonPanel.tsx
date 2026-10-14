import { useCallback, useEffect, useRef, useState } from 'react';
import { get, post, put } from '../../api/client';
import type { DungeonSettings, DungeonStatus, DungeonView, GuardStartResult } from '../../api/types';
import { reportClientError } from '../../diag/report';
import { CombatSection } from './CombatSection';
import './guard.css';
import './daily.css';
import './handoff.css';
import './supply.css';

// 副本 (services/dungeon.py): plays one dungeon script. The character walks to
// the entrance by itself, takes the daily mission, runs the floors and reports.
// The maps are shared with other players, so a run picks up wherever the
// dungeon is: an exit someone else opened is simply taken.
const VIEW_MS = 5000;
const STATUS_MS = 1000;
const SAVE_DELAY_MS = 400;

const PHASE_LABEL: Record<DungeonStatus['log'][number]['phase'], string> = {
  sent: '送出', confirmed: '完成', unconfirmed: '未確認', error: '錯誤', info: '',
};

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
const hhmm = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false, hour: '2-digit', minute: '2-digit' });
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

export function DungeonPanel({ pid, dungeon, active }: { pid: number; dungeon: string; active: boolean }) {
  const base = `/api/characters/${pid}/dungeon/${dungeon}`;
  const [view, setView] = useState<DungeonView | null>(null);
  const [status, setStatus] = useState<DungeonStatus | null>(null);
  const [settings, setSettings] = useState<DungeonSettings | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dirty = useRef(false);
  const saveTimer = useRef<number | null>(null);

  const loadView = useCallback(async () => {
    try {
      const v = await get<DungeonView>(base);
      setView(v);
      setStatus(v.status);
      if (!dirty.current) setSettings({ combat: v.combat, config: v.config });
    } catch (e) {
      reportClientError(e, { component: 'DungeonPanel.view', silent: true });
    }
  }, [base]);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await get<DungeonStatus>(`${base}/status`));
    } catch (e) {
      reportClientError(e, { component: 'DungeonPanel.status', silent: true });
    }
  }, [base]);

  useEffect(() => {
    if (!active) return;
    loadView();
    const v = window.setInterval(loadView, VIEW_MS);
    const s = window.setInterval(loadStatus, STATUS_MS);
    return () => { window.clearInterval(v); window.clearInterval(s); };
  }, [active, loadView, loadStatus]);

  const update = (next: DungeonSettings) => {
    setSettings(next);
    dirty.current = true;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      try {
        await put(`${base}/settings`, next);
        setNotice(null);
      } catch (e) {
        setNotice('設定沒有存到：角色可能還沒定位');
        reportClientError(e, { component: 'DungeonPanel.save', silent: true });
      } finally {
        dirty.current = false;
      }
    }, SAVE_DELAY_MS);
  };

  if (!view || !settings) return <section className="gd-panel gd-dim">讀取副本設定…</section>;

  const st = status ?? view.status;
  const running = st.running;
  const rec = view.record;
  const doneToday = rec.done && rec.date === today();
  const cooling = !running && view.cooldown_until != null;
  const { combat, config } = settings;
  const noAttack = !combat.basic && !combat.opener && !(combat.rotation?.length);
  const at = st.floor ? view.floors.indexOf(st.floor) : -1;

  const toggle = async () => {
    setBusy(true);
    try {
      if (running) {
        await post(`${base}/stop`);
      } else {
        const r = await post<GuardStartResult>(`${base}/start`);
        setNotice(r.ok ? null : r.reason ?? '無法開始');
      }
      await loadStatus();
    } catch (e) {
      reportClientError(e, { component: 'DungeonPanel.toggle' });
    } finally {
      setBusy(false);
    }
  };

  const stateText = running ? (st.problem ? '等待中' : st.moving ? '移動中' : '進行中')
    : doneToday ? '今天完成' : st.outcome === 'error' ? '已停止' : '待命';

  return (
    <div className="gd">
      <section className="gd-panel gd-strip" aria-label={`${view.title}執行`}>
        <span className={`gd-state${running ? ' is-on' : ''}${running && st.problem ? ' is-warn' : ''}`}>
          <i />{stateText}
        </span>
        <div className="gd-strip-text">
          <span className="gd-title">{view.title}</span>
          <span className="gd-dim">
            {running ? st.problem ?? st.step ?? '準備中' : '從任何地圖開始都可以：自己走到入口、接任務、打完回報；補水和 buff 交給常駐守護'}
          </span>
        </div>
        <button type="button" className={`dl-btn${running ? ' is-stop' : ''}`} onClick={toggle}
          disabled={busy || (!running && (!view.hook_ready || noAttack || cooling))}>
          {running ? '停止' : `開始${view.title}`}
        </button>
      </section>
      {notice && <div className="gd-notice" role="status">{notice}</div>}
      {st.warning && running && <div className="gd-notice" role="status">{st.warning}</div>}
      {cooling && view.cooldown_until != null && (
        <div className="gd-notice">還在冷卻，{hhmm(view.cooldown_until)} 後可以再進。工具只記得自己帶進去的那次。</div>
      )}
      {!view.hook_ready && !running && (
        <div className="gd-notice">副本要透過 hook 走路、打怪、對話；這個遊戲視窗的 hook 沒有連上，或缺少需要的指令。</div>
      )}
      {!running && noAttack && <div className="gd-notice">還沒設定攻擊方式：在下面「戰鬥」勾普攻，或選至少一個技能。</div>}

      <section className="gd-panel">
        <header className="gd-head">
          <h3>進度</h3>
          <span className="gd-dim">{running && st.area ? `目前在${st.area}` : doneToday ? '今天打完了' : '還沒開始'}</span>
        </header>
        <ol className="dl-ladder" aria-label="樓層進度" style={{ gridTemplateColumns: `repeat(${view.floors.length}, minmax(0, 1fr))` }}>
          {view.floors.map((f, i) => {
            const done = doneToday && !running ? true : running && at > i;
            const cur = running && at === i;
            return (
              <li key={f} className={done ? 'is-done' : cur ? 'is-cur' : ''} aria-current={cur ? 'step' : undefined}>
                <b>{f}</b>
                <span>{done ? '完成' : cur ? '進行中' : ''}</span>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="gd-panel ho-stats" aria-label="副本紀錄">
        <div>
          <span className="ho-lbl">今天</span>
          <span className="ho-val">{doneToday ? '已完成' : '還沒完成'}</span>
        </div>
        <div>
          <span className="ho-lbl">上次寶箱</span>
          <span className="ho-num">{rec.finished_at ? rec.chests : '—'}</span>
        </div>
        <div>
          <span className="ho-lbl">上次進場</span>
          <span className="ho-val">{rec.entered_at ? `${new Date(rec.entered_at * 1000).toLocaleDateString('zh-TW')} ${hhmm(rec.entered_at)}` : '—'}</span>
        </div>
        <div>
          <span className="ho-lbl">上次結束</span>
          <span className="ho-val" title={rec.ended ?? undefined}>{rec.ended ?? '—'}</span>
        </div>
      </section>

      <section className="gd-panel">
        <header className="gd-head"><h3>副本設定</h3></header>
        <label className="dl-check">
          <input type="checkbox" checked={config.mission ?? true}
            onChange={e => update({ ...settings, config: { ...config, mission: e.target.checked } })} />
          進場前接每日任務、打完回報（多拿一個寶箱和經驗；出口的寶箱不接也拿得到）
        </label>
      </section>

      <CombatSection combat={combat} skills={view.skills}
        onChange={c => update({ ...settings, combat: c })} />

      <section className="gd-panel">
        <header className="gd-head">
          <h3>{view.title}紀錄</h3>
          <span className="gd-dim">{!running && st.ended ? `上次：${st.ended}` : '最新的在下面'}</span>
        </header>
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
