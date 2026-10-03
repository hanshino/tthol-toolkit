import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { get, post } from '../../api/client';
import type { DamageEvent, DamageSnapshot, DamageStatus, DamageSummary, DamageTarget } from '../../api/types';
import { describeError, reportClientError } from '../../diag/report';
import './damage.css';

// Live view of the damage recorder (services/damage_capture.py). The backend
// keeps recording while this tab is hidden; the tab only polls while visible.
const POLL_MS = 500;
const HIT_WINDOW = 120; // seconds of hits drawn in the per-hit chart
const LOG_ROWS = 100;
const WINDOWS = [5, 10, 30] as const;

// Segment types in the result table.
const T_HIT = 0, T_CRIT = 1, T_HEAL = 4;
// 5 / 6 arrive as their own result when a skill's status (卸冑) lands or not;
// read from live data, not confirmed in the client script.
const RESULT_LABEL: Record<number, string> = { 0: '命中', 1: '重擊', 2: '未命中', 3: '未命中', 4: '補血', 5: '狀態命中', 6: '狀態未中' };
const MISS_TYPES = new Set([2, 3]);

// The target's debuffs when the hit landed: the post-hit read is empty on a kill.
function debuffsAt(t: DamageTarget | null | undefined): number[] {
  if (!t) return [];
  return t.debuffs_before ?? t.debuffs ?? [];
}

type Source = 'atk' | 'crit' | 'skill';
const SOURCE_VAR: Record<Source, string> = { atk: '--dmg-atk', crit: '--dmg-crit', skill: '--dmg-skill' };

type Hit = DamageEvent & { kind: 'hit' };

function sourceOf(h: Hit): Source {
  if (h.path === 'skill') return 'skill';
  return (h.segments ?? []).some(s => s[0] === T_CRIT) ? 'crit' : 'atk';
}

function skillLabel(h: Hit): string {
  const s = h.skill;
  if (!s) return '技能';
  if (s.method === 'learned' && s.name) return s.name;
  // Several learned skills share this cast effect: name them all.
  const names = (s.candidate_names ?? []).filter((n): n is string => !!n);
  if (s.method === 'ambiguous' && names.length) return `${names.join('／')}？`;
  if (s.name) return `${s.name}？`;
  return `技能 #${s.cast_effect}`;
}

function sourceLabel(h: Hit): string {
  const src = sourceOf(h);
  return src === 'skill' ? skillLabel(h) : src === 'crit' ? '重擊' : '普攻';
}

const fmt = (n: number) => Math.round(n).toLocaleString('en-US');
const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const pct = (r: number) => `${(r * 100).toFixed(1)}%`;
const kFmt = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : String(Math.round(v)));

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

export function DamageTab({ pid, active }: { pid: number; active: boolean }) {
  const [status, setStatus] = useState<DamageStatus | null>(null);
  const [events, setEvents] = useState<DamageEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [win, setWin] = useState<number>(10);
  const seqRef = useRef(0);

  const poll = useCallback(async () => {
    try {
      const s = await get<DamageStatus>(`/api/characters/${pid}/damage/status?since=${seqRef.current}`);
      if (s.seq < seqRef.current) {
        // Cleared (here or elsewhere): start the local log over.
        seqRef.current = 0;
        const full = await get<DamageStatus>(`/api/characters/${pid}/damage/status?since=0`);
        seqRef.current = full.seq;
        setEvents(full.events);
        setStatus(full);
      } else {
        seqRef.current = s.seq;
        if (s.events.length) setEvents(prev => [...prev, ...s.events]);
        setStatus(s);
      }
      setError(null);
    } catch (e) {
      // Polled every 500 ms: shown, not reported, so one outage is not 120 reports a minute.
      setError(`讀取錄製狀態失敗：${describeError(e)}`);
    }
  }, [pid]);

  useEffect(() => {
    if (!active) return;
    poll();
    const id = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(id);
  }, [active, poll]);

  const act = async (path: 'start' | 'stop' | 'clear') => {
    setBusy(true);
    try {
      await post(`/api/characters/${pid}/damage/${path}`);
      if (path === 'clear') { seqRef.current = 0; setEvents([]); }
      await poll();
    } catch (e) {
      setError(`操作失敗：${describeError(e)}`);
      reportClientError(e, { component: `DamageTab.${path}` });
    } finally {
      setBusy(false);
      setConfirmClear(false);
    }
  };

  const allHits = useMemo(() => events.filter((e): e is Hit => e.kind === 'hit'), [events]);
  // Skills none of the learned skills could cast are probably a party member's:
  // listed in the log, left out of the charts (the backend summary skips them too).
  const hits = useMemo(() => allHits.filter(h => !h.not_mine_suspect), [allHits]);
  const state = status?.status ?? 'idle';
  const recording = state === 'recording' || state === 'waiting';
  const elapsed = status?.elapsed ?? 0;
  const summary = status?.summary;
  const lastTarget = [...hits].reverse().find(h => h.target)?.target ?? null;

  return (
    <div className="dmg">
      <div className="dmg-bar">
        <StatusPill state={state} note={status?.note ?? null} elapsed={elapsed} />
        <span className="dmg-spacer" />
        {recording
          ? <button type="button" className="is-primary" disabled={busy} onClick={() => act('stop')}>暫停</button>
          : <button type="button" className="is-primary" disabled={busy} onClick={() => act('start')}>
            {state === 'paused' ? '繼續錄製' : '開始錄製'}
          </button>}
        {confirmClear
          ? <button type="button" className="dmg-danger" disabled={busy} onClick={() => act('clear')}>確定清除？</button>
          : <button type="button" disabled={busy || allHits.length === 0} onClick={() => setConfirmClear(true)}>清除</button>}
        {allHits.length > 0
          ? <a className="tt-btn" href={`/api/characters/${pid}/damage/export`} download>匯出 JSONL</a>
          : <button type="button" disabled>匯出 JSONL</button>}
      </div>
      {error && <div role="status" className="dmg-error">{error}</div>}

      {state === 'idle' && allHits.length === 0
        ? <EmptyState />
        : (
          <>
            <div className="dmg-top">
              <TargetCard target={lastTarget} />
              {summary && <Tiles summary={summary} />}
            </div>
            <div className="dmg-main">
              <div className="dmg-charts">
                <section className="dmg-panel">
                  <div className="dmg-head">
                    <h3>DPS 走勢</h3>
                    <div className="dmg-seg" role="group" aria-label="平滑視窗">
                      {WINDOWS.map(w => (
                        <button key={w} type="button" aria-pressed={win === w} onClick={() => setWin(w)}>{w} 秒</button>
                      ))}
                    </div>
                  </div>
                  <DpsChart hits={hits} elapsed={elapsed} win={win} gap={summary?.gap_seconds ?? 5} />
                </section>
                <section className="dmg-panel">
                  <div className="dmg-head">
                    <h3>每一下傷害 · 近 {HIT_WINDOW / 60} 分鐘</h3>
                    <Legend />
                  </div>
                  <HitChart hits={hits} elapsed={elapsed} />
                </section>
                <section className="dmg-panel">
                  <div className="dmg-head"><h3>傷害來源</h3></div>
                  <Sources hits={hits} total={summary?.total_damage ?? 0} />
                </section>
              </div>
              <SnapshotCard snapshot={status?.snapshot ?? null} />
            </div>
            <HitLog hits={allHits} />
          </>
        )}
    </div>
  );
}

function StatusPill({ state, note, elapsed }: { state: string; note: string | null; elapsed: number }) {
  const label = state === 'recording' ? '錄製中' : state === 'waiting' ? (note ?? '等待中') : state === 'paused' ? '已暫停' : '未錄製';
  return (
    <span className="dmg-status" data-state={state}>
      <span className="dmg-dot" aria-hidden="true" />
      <span>{label}</span>
      <span className="dmg-mono">{mmss(elapsed)}</span>
    </span>
  );
}

function EmptyState() {
  return (
    <div className="ws-empty dmg-empty">
      按「開始錄製」後去打怪，每一下傷害都會記在這裡。
      <br />
      只讀遊戲記憶體，不會改動遊戲。英雄和其他玩家的傷害不會算進來。
    </div>
  );
}

function TargetCard({ target }: { target: DamageTarget | null }) {
  if (!target) {
    return <section className="dmg-panel dmg-target"><div className="dmg-lbl">目前目標</div><div className="dmg-dim">還沒有打到怪</div></section>;
  }
  const debuffs = debuffsAt(target);
  return (
    <section className="dmg-panel dmg-target">
      <div className="dmg-lbl">目前目標</div>
      <div className="dmg-tname">{target.name ?? `npc ${target.npc_id}`}</div>
      <div className="dmg-tmeta">
        {target.level != null && <span>Lv {target.level}</span>}
        {target.defense != null && <span>防禦 {target.defense}</span>}
        {target.mdefense != null && <span>護勁 {target.mdefense}</span>}
        <span className="dmg-mono dmg-dim">#{target.instance}</span>
      </div>
      {target.hp_pct != null && (
        <div className="dmg-hp" title="怪物只同步血量百分比">
          <span className="dmg-hpbar"><i style={{ width: `${target.hp_pct}%` }} /></span>
          <span className="dmg-mono">{target.hp_pct}%</span>
        </div>
      )}
      <div className="dmg-chips">
        {debuffs.length === 0
          ? <span className="dmg-chip">無減益</span>
          : debuffs.map(g => <span key={g} className="dmg-chip is-debuff">{g === 15 ? '卸冑 · 防禦 ×0.4' : `減益 ${g}`}</span>)}
      </div>
    </section>
  );
}

function Tiles({ summary: s }: { summary: DamageSummary }) {
  const tiles: { l: string; v: string; sub: string; hero?: boolean }[] = [
    { l: '戰鬥 DPS', v: fmt(s.combat_dps), sub: `只算戰鬥時間 ${mmss(s.combat_seconds)}`, hero: true },
    { l: '整體 DPS', v: fmt(s.overall_dps), sub: `含找怪走路 ${mmss(s.elapsed_seconds)}` },
    { l: '總傷害', v: s.total_damage >= 1e6 ? `${(s.total_damage / 1e6).toFixed(2)}M` : fmt(s.total_damage), sub: `${s.hits} 下` },
    { l: '重擊率', v: pct(s.crit_rate), sub: '普攻命中中的重擊' },
    { l: '未命中', v: pct(s.miss_rate), sub: `${s.misses} 段` },
    { l: '卸冑期間', v: `${Math.round(s.debuffed_share * 100)}%`, sub: '出手時目標身上有減益' },
  ];
  return (
    <div className="dmg-tiles">
      {tiles.map(t => (
        <div key={t.l} className="dmg-tile">
          <div className="dmg-lbl">{t.l}</div>
          <div className={t.hero ? 'dmg-v is-hero' : 'dmg-v'}>{t.v}</div>
          <div className="dmg-sub">{t.sub}</div>
        </div>
      ))}
    </div>
  );
}

function Legend() {
  return (
    <div className="dmg-legend">
      <span><i style={{ background: 'var(--dmg-atk)' }} />普攻</span>
      <span><i style={{ background: 'var(--dmg-crit)' }} />重擊</span>
      <span><i style={{ background: 'var(--dmg-skill)' }} />技能</span>
      <span><i className="is-miss" />未命中</span>
    </div>
  );
}

// ---- charts -----------------------------------------------------------

const W = 760;
const M = { l: 52, r: 16, t: 14, b: 28 };

function Axes({ H, xMin, xMax, yMax }: { H: number; xMin: number; xMax: number; yMax: number }) {
  const span = xMax - xMin;
  const step = span > 600 ? 120 : span > 240 ? 60 : span > 100 ? 30 : span > 50 ? 15 : 10;
  const ticks: number[] = [];
  for (let x = Math.ceil(xMin / step) * step; x <= xMax + 1e-9; x += step) ticks.push(x);
  const X = (x: number) => M.l + ((W - M.l - M.r) * (x - xMin)) / span;
  return (
    <g className="dmg-axis">
      {[0, 1, 2, 3, 4].map(i => {
        const y = M.t + (H - M.t - M.b) * (1 - i / 4);
        return (
          <g key={i}>
            <line x1={M.l} x2={W - M.r} y1={y} y2={y} className="dmg-grid" />
            <text x={M.l - 8} y={y + 4} textAnchor="end">{kFmt((yMax * i) / 4)}</text>
          </g>
        );
      })}
      {ticks.map(x => <text key={x} x={X(x)} y={H - M.b + 18} textAnchor="middle">{mmss(x)}</text>)}
      <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} className="dmg-base" />
    </g>
  );
}

function Tip({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  return <div className="dmg-tip" style={{ left: `${(x / W) * 100}%`, top: y }}>{children}</div>;
}

// Rolling DPS sampled every half second. Long gaps between hits (walking to
// the next monster) are shaded and break the line instead of dragging it to 0.
function DpsChart({ hits, elapsed, win, gap }: { hits: Hit[]; elapsed: number; win: number; gap: number }) {
  const H = 220;
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const { segments, idle, xMax, yMax, points } = useMemo(() => {
    const xMax = Math.max(30, elapsed);
    const times = hits.map(h => h.t);
    const idle: [number, number][] = [];
    const lead = times.length ? times[0] : xMax;
    if (lead > gap) idle.push([0, lead]);
    for (let i = 1; i < times.length; i++) if (times[i] - times[i - 1] > gap) idle.push([times[i - 1], times[i]]);
    const tail = times.length ? times[times.length - 1] : 0;
    if (times.length && elapsed - tail > gap) idle.push([tail, elapsed]);
    const inIdle = (x: number) => idle.some(([a, b]) => x > a + 0.25 && x < b);
    const step = Math.max(0.5, Math.ceil((xMax / 600) * 2) / 2); // at most ~600 points
    const points: { x: number; v: number }[] = [];
    let j = 0, sum = 0, k = 0;
    for (let x = step; x <= elapsed + 1e-9; x += step) {
      while (j < hits.length && hits[j].t <= x) sum += hits[j++].damage ?? 0;
      while (k < j && hits[k].t <= x - win) sum -= hits[k++].damage ?? 0;
      points.push({ x, v: inIdle(x) ? NaN : sum / Math.min(win, x) });
    }
    const segments: { x: number; v: number }[][] = [];
    let cur: { x: number; v: number }[] = [];
    for (const p of points) {
      if (Number.isNaN(p.v)) { if (cur.length) segments.push(cur); cur = []; } else cur.push(p);
    }
    if (cur.length) segments.push(cur);
    const peak = Math.max(1, ...points.filter(p => !Number.isNaN(p.v)).map(p => p.v));
    return { segments, idle, xMax, yMax: niceMax(peak * 1.1), points };
  }, [hits, elapsed, win, gap]);

  const X = (x: number) => M.l + ((W - M.l - M.r) * x) / xMax;
  const Y = (v: number) => M.t + (H - M.t - M.b) * (1 - v / yMax);
  const valid = points.filter(p => !Number.isNaN(p.v));
  const last = valid[valid.length - 1];
  const hp = hover != null ? points[hover] : null;

  const onMove = (e: React.PointerEvent) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r || !points.length) return;
    const sx = ((e.clientX - r.left) * W) / r.width;
    const xv = ((sx - M.l) / (W - M.l - M.r)) * xMax;
    let best = 0;
    for (let i = 1; i < points.length; i++) if (Math.abs(points[i].x - xv) < Math.abs(points[best].x - xv)) best = i;
    setHover(best);
  };

  return (
    <div className="dmg-plot">
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="DPS 走勢">
        <defs>
          <linearGradient id="dmg-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--tt-gold)" stopOpacity="0.28" />
            <stop offset="1" stopColor="var(--tt-gold)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {idle.map(([a, b]) => (
          <rect key={a} x={X(a)} y={M.t} width={Math.max(0, X(Math.min(b, xMax)) - X(a))} height={H - M.t - M.b} className="dmg-idle" />
        ))}
        <Axes H={H} xMin={0} xMax={xMax} yMax={yMax} />
        {segments.map((seg, i) => {
          const d = seg.map((p, n) => `${n ? 'L' : 'M'}${X(p.x).toFixed(1)},${Y(p.v).toFixed(1)}`).join('');
          return (
            <g key={i}>
              <path d={`${d}L${X(seg[seg.length - 1].x)},${Y(0)}L${X(seg[0].x)},${Y(0)}Z`} fill="url(#dmg-area)" />
              <path d={d} className="dmg-line" />
            </g>
          );
        })}
        {last && (
          <>
            <circle cx={X(last.x)} cy={Y(last.v)} r={4} className="dmg-end" />
            <text x={X(last.x) - 8} y={Y(last.v) - 10} textAnchor="end" className="dmg-endlabel">{fmt(last.v)}</text>
          </>
        )}
        {hp && !Number.isNaN(hp.v) && (
          <>
            <line x1={X(hp.x)} x2={X(hp.x)} y1={M.t} y2={H - M.b} className="dmg-cross" />
            <circle cx={X(hp.x)} cy={Y(hp.v)} r={4} className="dmg-end" />
          </>
        )}
        <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} fill="transparent"
          onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {hp && (
        <Tip x={X(hp.x)} y={((Number.isNaN(hp.v) ? Y(0) : Y(hp.v)) / H) * (ref.current?.getBoundingClientRect().height ?? H)}>
          <span className="dmg-mono">{mmss(hp.x)}</span> · 近 {win} 秒<br />
          {Number.isNaN(hp.v) ? '沒在戰鬥' : <>DPS <b>{fmt(hp.v)}</b></>}
        </Tip>
      )}
      {idle.length > 0 && <div className="dmg-note">灰色區段是兩下間隔超過 {gap} 秒（找怪、走路），不算戰鬥時間。</div>}
    </div>
  );
}

function HitChart({ hits, elapsed }: { hits: Hit[]; elapsed: number }) {
  const H = 200;
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<Hit | null>(null);
  const xMax = Math.max(HIT_WINDOW, elapsed);
  const xMin = xMax - HIT_WINDOW;
  const shown = hits.filter(h => h.t >= xMin);
  const yMax = niceMax(Math.max(1, ...shown.map(h => h.damage ?? 0)) * 1.05);
  const X = (x: number) => M.l + ((W - M.l - M.r) * (x - xMin)) / HIT_WINDOW;
  const Y = (v: number) => M.t + (H - M.t - M.b) * (1 - v / yMax);
  const bw = 3;

  return (
    <div className="dmg-plot">
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="每一下傷害">
        <Axes H={H} xMin={xMin} xMax={xMax} yMax={yMax} />
        {shown.map(h => {
          const dmg = h.damage ?? 0;
          const x = X(h.t);
          return (
            <g key={h.seq}>
              {dmg === 0 && !(h.segments ?? []).some(s => MISS_TYPES.has(s[0])) ? null : dmg > 0
                ? <rect x={x - bw / 2} y={Y(dmg)} width={bw} height={Math.max(1, Y(0) - Y(dmg))} rx={1}
                  style={{ fill: `var(${SOURCE_VAR[sourceOf(h)]})` }} />
                : <circle cx={x} cy={Y(0) - 6} r={3.5} className="dmg-miss" />}
              <rect x={x - 5} y={M.t} width={10} height={H - M.t - M.b} fill="transparent"
                onPointerEnter={() => setHover(h)} onPointerLeave={() => setHover(null)} />
            </g>
          );
        })}
      </svg>
      {hover && (
        <Tip x={X(hover.t)} y={(Y(hover.damage ?? 0) / H) * (ref.current?.getBoundingClientRect().height ?? H)}>
          <span className="dmg-mono">{mmss(hover.t)}</span> · {sourceLabel(hover)}<br />
          {(hover.segments ?? []).map((s, i) => (
            <span key={i}>{RESULT_LABEL[s[0]] ?? `類型 ${s[0]}`}{s[1] ? <> <b>{fmt(s[1])}</b></> : null}{i < (hover.segments ?? []).length - 1 ? '、' : ''}</span>
          ))}
          {hover.target && <><br />{hover.target.name ?? `npc ${hover.target.npc_id}`}{debuffsAt(hover.target).includes(15) ? ' · 卸冑' : ''}</>}
        </Tip>
      )}
    </div>
  );
}

function Sources({ hits, total }: { hits: Hit[]; total: number }) {
  const rows = useMemo(() => {
    const by = new Map<string, { label: string; src: Source; n: number; sum: number; min: number; max: number }>();
    for (const h of hits) {
      for (const [type, value] of h.segments ?? []) {
        if (type !== T_HIT && type !== T_CRIT) continue;
        const src: Source = h.path === 'skill' ? 'skill' : type === T_CRIT ? 'crit' : 'atk';
        const label = src === 'skill' ? skillLabel(h) : src === 'crit' ? '重擊' : '普攻';
        const r = by.get(label) ?? { label, src, n: 0, sum: 0, min: Infinity, max: 0 };
        r.n++; r.sum += value; r.min = Math.min(r.min, value); r.max = Math.max(r.max, value);
        by.set(label, r);
      }
    }
    return [...by.values()].sort((a, b) => b.sum - a.sum);
  }, [hits]);
  if (!rows.length) return <div className="dmg-dim">還沒有傷害</div>;
  return (
    <>
      <div className="dmg-share" aria-hidden="true">
        {rows.map(r => <i key={r.label} style={{ width: `${(r.sum / (total || 1)) * 100}%`, background: `var(${SOURCE_VAR[r.src]})` }} />)}
      </div>
      <table className="dmg-table">
        <thead><tr><th>來源</th><th>段數</th><th>平均</th><th>最低–最高</th><th>佔總傷害</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.label}>
              <td><span className="dmg-sw" style={{ background: `var(${SOURCE_VAR[r.src]})` }} />{r.label}</td>
              <td>{r.n}</td><td>{fmt(r.sum / r.n)}</td><td>{fmt(r.min)}–{fmt(r.max)}</td>
              <td>{total ? ((r.sum / total) * 100).toFixed(1) : '0'}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function SnapshotCard({ snapshot: s }: { snapshot: DamageSnapshot | null }) {
  return (
    <aside className="dmg-panel dmg-snap">
      <h3>角色快照</h3>
      {!s
        ? <div className="dmg-dim">讀取中…</div>
        : (
          <dl className="dmg-kv">
            <dt>等級</dt><dd>Lv {s.level}</dd>
            <dt>物攻</dt><dd>{fmt(s.panel.atk ?? 0)}</dd>
            <dt>內勁</dt><dd>{fmt(s.panel.matk ?? 0)}</dd>
            <dt>命中</dt><dd>{fmt(s.panel.hit ?? 0)}</dd>
            <dt>重擊值</dt><dd>{fmt(s.panel.critical ?? 0)}</dd>
            {s.weapons.map(w => (
              <WeaponRow key={w.slot} name={w.name ?? `#${w.item_id}`} plus={w.plus} slot={w.slot} />
            ))}
            <dt>身上 buff</dt>
            <dd className="dmg-text">{s.buffs.length ? s.buffs.map(b => b.name ?? b.group).join('、') : '無'}</dd>
          </dl>
        )}
      <p className="dmg-note">只列得出契、盾、陣、符類 buff；純加屬性的 buff 讀不到，效果已算在面板數值裡。快照只在數值變動時記一次。</p>
    </aside>
  );
}

function WeaponRow({ name, plus, slot }: { name: string; plus: number; slot: string }) {
  return (
    <>
      <dt>{slot === 'HAND_L' ? '左手' : '武器'}</dt>
      <dd className="dmg-text">{name}{plus > 0 ? ` +${plus}` : ''}</dd>
    </>
  );
}

function HitLog({ hits }: { hits: Hit[] }) {
  const rows = hits.slice(-LOG_ROWS).reverse();
  return (
    <section className="dmg-panel">
      <div className="dmg-head"><h3>出手紀錄</h3><span className="dmg-lbl">共 {hits.length} 下 · 顯示最近 {Math.min(LOG_ROWS, hits.length)} 下</span></div>
      <div className="dmg-logwrap">
        <table className="dmg-table dmg-log">
          <thead><tr><th>時間</th><th>來源</th><th>結果</th><th>傷害</th><th>目標</th><th>目標狀態</th><th>目標依據</th></tr></thead>
          <tbody>
            {rows.map(h => {
              const segs = h.segments ?? [];
              const t = h.target;
              return (
                <tr key={h.seq} data-miss={((h.damage ?? 0) === 0 && !h.damage_lost_suspect) || undefined}>
                  <td className="dmg-mono">{mmss(h.t)}.{Math.floor((h.t % 1) * 10)}</td>
                  <td><span className="dmg-sw" style={{ background: `var(${SOURCE_VAR[sourceOf(h)]})` }} />{sourceLabel(h)}</td>
                  <td>{segs.map(s => RESULT_LABEL[s[0]] ?? `類型 ${s[0]}`).join('、')}</td>
                  <td className="dmg-num">{h.damage_lost_suspect
                    ? <span className="dmg-tag is-guess" title="這招觸發了狀態，傷害那一筆被下一筆蓋掉、沒讀到">傷害遺失</span>
                    : segs.some(s => s[0] === T_HEAL) ? '—' : (h.damage ? fmt(h.damage) : '—')}</td>
                  <td>{t ? <>{t.name ?? `npc ${t.npc_id}`} <span className="dmg-dim dmg-mono">#{t.instance}</span></> : <span className="dmg-dim">不明</span>}</td>
                  <td>{debuffsAt(t).length ? <span className="dmg-tag is-debuff">{debuffsAt(t).includes(15) ? '卸冑' : '有減益'}</span> : <span className="dmg-dim">—</span>}</td>
                  <td><TargetSource hit={h} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}


function TargetSource({ hit }: { hit: Hit }) {
  const t = hit.target;
  if (hit.not_mine_suspect) {
    return <span className="dmg-tag is-guess" title="這招不在你學過的技能裡，可能是隊友打的，不算進統計">疑似他人</span>;
  }
  if (!t) return <span className="dmg-dim">—</span>;
  if (t.source === 'packet') return <span className="dmg-tag">封包</span>;
  if (t.source === 'selected') return <span className="dmg-tag" title="技能封包沒有目標，取你目前選取的對象">選取目標</span>;
  return <span className="dmg-tag is-guess" title="技能封包沒有目標，也沒有選取對象，取最近一次普攻的對象">推定</span>;
}
