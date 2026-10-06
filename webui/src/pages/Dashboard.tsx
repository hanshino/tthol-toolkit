import { useEffect, useState } from 'react';
import { post } from '../api/client';
import type { CharacterRow, DailyMetric, DailyQueueItem, DailyStartResult, DailySummary } from '../api/types';
import { friendlyError } from '../components/friendlyError';
import { reportClientError } from '../diag/report';
import { can, isStopped, isUnlocated, type OpenChar } from '../nav';
import { Bar, LinkDot, Panel } from '../primitives';
import './dashboard.css';

// 總覽: a card per character showing its 日常 queue (services/daily.py). The
// cards render only the generic DailySummary; they know no module.
const LOW_HP = 0.3;

type Alert = { pid: number; text: string; tone: 'bad' | 'warn' };
type Tone = 'idle' | 'move' | 'run' | 'done' | 'err';

const TONE: Record<DailySummary['state'], Tone> = {
  idle: 'idle', moving: 'move', running: 'run', done: 'done', done_today: 'done', stopped: 'err',
};
const STATE_LABEL: Record<DailySummary['state'], string> = {
  idle: '未開始', moving: '前往中', running: '進行中', done: '完成', done_today: '今日已做', stopped: '停下',
};
const SEG_LABEL: Record<DailySummary['segments'][number], string> = {
  empty: '', done: '已完成', skipped: '用狐光靈珠略過', current: '進行中', target: '目標', error: '停在這裡',
};
const CHIP_NOTE: Partial<Record<DailyQueueItem['state'], string>> = {
  running: '進行中', moving: '前往中', error: '停下', halted: '停住', skipped: '今日已做',
};

const mmss = (secs: number) => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`;

function label(c: CharacterRow): string {
  return isUnlocated(c) ? `pid ${c.pid}` : c.name;
}

function alertsFor(chars: CharacterRow[]): Alert[] {
  const out: Alert[] = [];
  for (const c of chars) {
    // The error says what to do, so it wins over the generic "stopped".
    if (c.last_error) {
      const what = c.last_error.code === 'E_LOCATE_EXHAUSTED' ? '定位失敗' : '出錯';
      out.push({ pid: c.pid, text: `${label(c)} ${what}`, tone: 'bad' });
    } else if (isStopped(c)) {
      out.push({ pid: c.pid, text: `${label(c)} 偵測已停止`, tone: 'warn' });
    } else if (c.vitals.hp_max > 0 && c.vitals.hp / c.vitals.hp_max < LOW_HP) {
      out.push({ pid: c.pid, text: `${c.name} 氣血偏低`, tone: 'bad' });
    }
    if (c.daily?.error) out.push({ pid: c.pid, text: `${c.name} ${c.daily.error}`, tone: 'bad' });
  }
  return out;
}

const busy = (c: CharacterRow) => c.daily?.running || ['running', 'moving'].includes(c.daily?.card.state ?? '');
// 日常 modules work through the hook's commands: shown when the hook allows
// one, or while one still runs / reports a stop after the hook went away.
const showsDaily = (c: CharacterRow) => !!c.daily && (can(c, 'daily.') || busy(c) || !!c.daily.error);
const selectable = (c: CharacterRow) => !isUnlocated(c) && !isStopped(c) && !!c.daily && can(c, 'daily.') && !busy(c);

// Ticks once a second while some card shows a timer (the snapshot comes every 3 s).
function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [on]);
  return now;
}

export function Dashboard({ chars, onOpenChar }: { chars: CharacterRow[]; onOpenChar: OpenChar }) {
  const alerts = alertsFor(chars);
  const autoclicking = chars.filter(c => c.autoclick.running);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [sending, setSending] = useState(false);
  const [fails, setFails] = useState<string[]>([]);
  const timers = chars.some(c => (c.daily?.card.metrics ?? []).some(m => m.since != null));
  const now = useNow(timers);

  const ready = chars.filter(c => picked.has(c.pid) && selectable(c));
  const anyRunning = chars.some(busy);
  const daily = chars.filter(showsDaily);
  const count = (s: Tone[]) => daily.filter(c => s.includes(TONE[c.daily!.card.state])).length;
  const tally: [string, number][] = [
    ['進行中', count(['run', 'move'])], ['做完', count(['done'])], ['停下', count(['err'])], ['未開始', count(['idle'])],
  ];

  const toggle = (pid: number, on: boolean) => setPicked(prev => {
    const next = new Set(prev);
    if (on) next.add(pid); else next.delete(pid);
    return next;
  });
  const pickUndone = () => setPicked(new Set(
    chars.filter(c => selectable(c) && !['done', 'done_today'].includes(c.daily!.card.state)).map(c => c.pid),
  ));
  const start = async () => {
    setSending(true);
    try {
      const results = await post<DailyStartResult[]>('/api/daily/start', { pids: ready.map(c => c.pid) });
      const name = (pid: number) => chars.find(c => c.pid === pid)?.name ?? `pid ${pid}`;
      setFails(results.filter(r => !r.ok).map(r => `${name(r.pid)}：${r.reason ?? '無法開始'}`));
      setPicked(new Set());
    } catch (e) {
      reportClientError(e, { component: 'Dashboard.start' });
    } finally {
      setSending(false);
    }
  };
  const stopAll = async () => {
    setSending(true);
    try {
      await post('/api/daily/stop');
    } catch (e) {
      reportClientError(e, { component: 'Dashboard.stopAll' });
    } finally {
      setSending(false);
    }
  };

  return (
    <div style={{ display: 'grid', gap: 12, padding: 16 }}>
      <Panel>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 13, letterSpacing: 4, color: 'var(--tt-dim)' }}>警示</span>
          {alerts.length === 0 && <span style={{ color: 'var(--tt-dim)', fontSize: 12 }}>無</span>}
          {alerts.map(a => (
            <button
              key={`${a.pid}-${a.text}`} type="button" onClick={() => onOpenChar(a.pid)}
              style={{
                fontSize: 12, padding: '4px 10px', letterSpacing: 1,
                color: a.tone === 'bad' ? 'var(--tt-bad)' : 'var(--tt-warn)',
                borderColor: a.tone === 'bad' ? 'var(--tt-bad)' : 'var(--tt-warn)',
              }}
            >{a.text}</button>
          ))}
          <span style={{ marginLeft: 'auto', fontSize: 12, color: autoclicking.length ? 'var(--tt-ok)' : 'var(--tt-dim)' }}>
            {autoclicking.length ? `● 輔助執行中：${autoclicking.map(c => c.name).join('、')}` : '輔助未啟用'}
          </span>
        </div>
      </Panel>

      {daily.length > 0 && (
        <Panel>
          <div className="db-batch">
            <div className="db-batch-l">
              <span className="db-ttl">日常</span>
              <div className="db-tally">
                {tally.map(([k, v]) => <span key={k}>{k}<b>{v}</b></span>)}
              </div>
            </div>
            <div className="db-batch-r">
              <button type="button" onClick={pickUndone} disabled={sending}>選今天還沒做完的</button>
              <button type="button" className="db-go" onClick={start} disabled={sending || ready.length === 0}>
                開始（{ready.length} 隻）
              </button>
              <button type="button" className="db-stop" onClick={stopAll} disabled={sending || !anyRunning}>全部停止</button>
            </div>
            <div className="db-note">
              每隻跑自己在「日常」分頁排好的清單，今天做過的項目自動略過，一項做完接下一項；一項出錯就停住整串，處理好再按開始會從停下的那項接著跑。不在目的地的會先自己走過去。
            </div>
            {fails.length > 0 && (
              <div className="db-fail" role="status">
                {fails.map(f => <span key={f}>沒有開始：{f}</span>)}
              </div>
            )}
          </div>
        </Panel>
      )}

      <Panel title="總覽">
        {chars.length === 0
          ? <div style={{ color: 'var(--tt-dim)', fontSize: 12, padding: 24, textAlign: 'center' }}>尚未偵測到遊戲程式 — 開啟遊戲後會自動出現在這裡</div>
          : (
            <div className="db-board">
              {chars.map(c => (
                <Card
                  key={c.pid} c={c} now={now}
                  picked={picked.has(c.pid)} onPick={on => toggle(c.pid, on)}
                  onOpen={() => onOpenChar(c.pid)} onOpenDaily={() => onOpenChar(c.pid, 'daily')}
                />
              ))}
            </div>
          )}
      </Panel>
    </div>
  );
}

function Card({ c, now, picked, onPick, onOpen, onOpenDaily }: {
  c: CharacterRow;
  now: number;
  picked: boolean;
  onPick: (on: boolean) => void;
  onOpen: () => void;
  onOpenDaily: () => void;
}) {
  const unlocated = isUnlocated(c);
  const card = showsDaily(c) ? c.daily?.card : undefined;
  const metrics = card?.metrics ?? [];
  const tone: Tone = card ? TONE[card.state] : 'idle';
  const canPick = selectable(c);
  const sub = unlocated
    ? `pid ${c.pid}`
    : [`Lv ${c.level}`, c.sect, c.position.map_name].filter(Boolean).join(' · ');
  return (
    <div className={`db-card${isStopped(c) ? ' is-lost' : ''}${picked && canPick ? ' is-picked' : ''}`} data-s={tone}>
      <div className="db-card-h">
        {card ? (
          <input
            type="checkbox" className="db-check" checked={picked && canPick} disabled={!canPick}
            onChange={e => onPick(e.target.checked)}
            title={busy(c) ? '執行中' : canPick ? undefined : !can(c, 'daily.') ? 'hook 斷線了' : '角色還沒定位'}
            aria-label={`選 ${label(c)}`}
          />
        ) : <span aria-hidden />}
        <button type="button" className="db-nm" onClick={onOpen} title="開啟角色">
          <b><LinkDot status={c.link} size={7} />{c.name}</b>
          <span>{sub}</span>
        </button>
        {card && <span className="db-pill" data-s={tone}>{STATE_LABEL[card.state]}</span>}
      </div>

      {card ? (
        <>
          <span className="db-mod">{card.title}</span>
          <div className="db-big">
            {card.headline && <b className={tone === 'idle' ? 'is-mute' : ''}>{card.headline}</b>}
            {card.where && <span>{card.where}</span>}
          </div>
          {card.segments.length > 0 && (
            <ol className="db-segs" style={{ gridTemplateColumns: `repeat(${card.segments.length}, minmax(0, 1fr))` }}
              aria-label={`${card.title}進度`}>
              {card.segments.map((k, i) => (
                <li key={i} data-k={k} aria-label={`第 ${i + 1} 格${SEG_LABEL[k] ? `，${SEG_LABEL[k]}` : ''}`} />
              ))}
            </ol>
          )}
          {metrics.length > 0 && (
            <div className="db-kv" style={{ gridTemplateColumns: `repeat(${metrics.length}, minmax(0, 1fr))` }}>
              {metrics.map(m => (
                <div key={m.label}><small>{m.label}</small><b className={m.highlight ? 'is-hl' : ''}>{metric(m, now)}</b></div>
              ))}
            </div>
          )}
          <div className={`db-step${tone === 'err' ? ' is-err' : ''}`}>{card.step ?? ''}</div>
        </>
      ) : unlocated ? (
        <div className="db-step">角色還沒定位</div>
      ) : null}

      {!unlocated && (
        <div className="db-vitals">
          <Vital tone="hp" v={c.vitals.hp} m={c.vitals.hp_max} />
          <Vital tone="mp" v={c.vitals.mp} m={c.vitals.mp_max} />
        </div>
      )}
      {c.last_error && (
        <span className="db-err">
          <span style={{ fontFamily: 'var(--tt-font-serif)', marginRight: 6 }}>錯</span>
          {friendlyError(c.last_error)} — 點名字進去處理
        </span>
      )}
      {card && c.daily && <Queue items={c.daily.items} onOpen={onOpenDaily} />}
    </div>
  );
}

function metric(m: DailyMetric, now: number): string {
  if (m.since != null) return mmss(Math.max(0, now / 1000 - m.since));
  return m.value ?? '—';
}

function Queue({ items, onOpen }: { items: DailyQueueItem[]; onOpen: () => void }) {
  return (
    <div className="db-queue">
      <span className="lbl">清單</span>
      {items.length === 0 && (
        <button type="button" onClick={onOpen} style={{ fontSize: 12, padding: '2px 8px' }}>到「日常」排清單</button>
      )}
      {items.map((it, i) => (
        <span key={it.module} style={{ display: 'contents' }}>
          {i > 0 && <span className="arr" aria-hidden>→</span>}
          <span className="db-q" data-s={it.state}>
            {it.title}
            {(it.state === 'done' ? it.result : CHIP_NOTE[it.state]) && (
              <small>{it.state === 'done' ? it.result : CHIP_NOTE[it.state]}</small>
            )}
          </span>
        </span>
      ))}
    </div>
  );
}

function Vital({ tone, v, m }: { tone: 'hp' | 'mp'; v: number; m: number }) {
  return (
    <span className="db-vit">
      <Bar value={v} max={m} tone={tone} height={5} />
      <span>{v}/{m}</span>
    </span>
  );
}
