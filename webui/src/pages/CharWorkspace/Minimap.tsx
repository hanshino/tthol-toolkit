import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { get, post } from '../../api/client';
import type { Minimap as MinimapData, MinimapExit, MinimapRegion, Position, WalkPlan, WalkStatus } from '../../api/types';
import './minimap.css';
import { WALK_FAILURES, WALK_REASONS } from '../../components/walk/messages';

const MAX_HEIGHT = 420; // px default; CSS can lower it via --mm-max-h
const REGION_MARGIN = 40; // map px kept around the cropped space (one tile)

// A move longer than this (tiles) is a teleport: the dot jumps, no slide.
const JUMP_TILES = 3;

const inside = (r: MinimapRegion, x: number, y: number) =>
  x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;

const WALK_POLL_MS = 400;

/** A previewed click-to-walk route: the clicked tile and the planner's answer. */
type Route = { tx: number; ty: number; plan: WalkPlan | null; failed: boolean };

type Layer = 'warps' | 'npcs' | 'spawns';
const LAYERS: { k: Layer; n: string }[] = [
  { k: 'warps', n: '出口' },
  { k: 'npcs', n: 'NPC' },
  { k: 'spawns', n: '怪物' },
];

/** Monster level vs the character: same bands as the 駐紮怪物 table. */
export function levelTone(level: number | null | undefined, charLevel: number): 'ok' | 'bad' | 'mute' {
  const delta = (level ?? 0) - charLevel;
  if (Math.abs(delta) <= 3) return 'ok';
  return delta > 3 ? 'bad' : 'mute';
}

/** A list row being hovered: light up one exit (by key) or a monster's spawns (by npc). */
export type MinimapHighlight = { exitKey: string } | { npcId: number } | null;

/** Destination names of an exit, for its label and its 出口 row. */
export const exitNames = (e: MinimapExit) => e.options.map((o) => o.name).join('／');

/** The stage's minimap payload; shared by the map and the 出口 list. */
export function useMinimapData(stageId: number | null) {
  const [data, setData] = useState<MinimapData | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setData(null);
    setFailed(false);
    if (stageId === null) return;
    let cancelled = false;
    get<MinimapData>(`/api/maps/${stageId}/minimap`)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [stageId]);
  return { data, failed };
}

export function Minimap({ pid, position, charLevel, data, failed, highlight = null }: {
  pid: number; position: Position; charLevel: number; data: MinimapData | null; failed: boolean;
  highlight?: MinimapHighlight;
}) {
  const stageId = position.stage_id ?? null;
  const [imgBroken, setImgBroken] = useState(false);
  const [shown, setShown] = useState<Record<Layer, boolean>>({ warps: true, npcs: true, spawns: true });
  const [region, setRegion] = useState<MinimapRegion | null>(null);
  const [fullMap, setFullMap] = useState(false);
  const [route, setRoute] = useState<Route | null>(null);
  const routeReq = useRef(0);
  const [walk, setWalk] = useState<WalkStatus | null>(null);
  const walking = walk?.state === 'walking';
  // A walk may already be running (started before this tab was opened).
  useEffect(() => {
    setWalk(null);
    let cancelled = false;
    get<WalkStatus>(`/api/characters/${pid}/walk`)
      .then((w) => { if (!cancelled && w.state === 'walking') setWalk(w); })
      .catch(() => { /* no walk */ });
    return () => { cancelled = true; };
  }, [pid]);
  // Poll the runner while it walks; the final state stays shown until the next action.
  useEffect(() => {
    if (!walking) return;
    let misses = 0;
    const t = window.setInterval(() => {
      get<WalkStatus>(`/api/characters/${pid}/walk`)
        .then((w) => { misses = 0; setWalk(w); })
        .catch(() => {
          misses += 1;
          if (misses >= 5) setWalk((w) => ({ ...(w ?? { legs: 0 }), state: 'failed', message: 'lost contact' }));
        });
    }, WALK_POLL_MS);
    return () => window.clearInterval(t);
  }, [walking, pid]);
  useEffect(() => { if (walk?.state === 'done') setRoute(null); }, [walk?.state]);
  const placed = position.px != null && position.py != null;

  useEffect(() => { setImgBroken(false); }, [stageId]);

  // The space the player stands in (city vs. one of the interiors packed into
  // the same map). Only re-asked once the player leaves the current box.
  useEffect(() => { setRegion(null); setRoute(null); routeReq.current++; }, [stageId]);
  const px = position.px ?? null;
  const py = position.py ?? null;
  const needRegion = stageId !== null && placed
    && (region === null || !inside(region, px!, py!));
  // Read through a ref: with the position updating every step, depending on
  // x/y would cancel and restart the request on each step until it lands.
  const tile = useRef({ x: position.x, y: position.y });
  tile.current = { x: position.x, y: position.y };
  useEffect(() => {
    if (!needRegion) return;
    let cancelled = false;
    const { x, y } = tile.current;
    get<MinimapRegion | null>(`/api/maps/${stageId}/region?x=${x}&y=${y}`)
      .then((r) => { if (!cancelled && r) setRegion(r); })
      .catch(() => { /* no walk mask: stay on the full map */ });
    return () => { cancelled = true; };
  }, [needRegion, stageId]);

  if (stageId === null) return <div className="mm-empty">讀不到地圖編號，無法顯示小地圖</div>;
  if (failed) return <div className="mm-empty">此地圖沒有小地圖資料</div>;
  if (!data) return <div className="mm-empty">讀取中…</div>;
  if (!data.width_px || !data.height_px) return <div className="mm-empty">此地圖沒有尺寸資料</div>;

  const W = data.width_px;
  const H = data.height_px;
  // View box in game coordinates (y grows upward): the player's space plus a
  // margin, or the whole map.
  // A lit exit outside the player's space widens the view to the whole map.
  const litExit = highlight !== null && 'exitKey' in highlight
    ? data.exits.find((e) => e.key === highlight.exitKey) ?? null
    : null;
  const litOutside = litExit !== null && region !== null && !inside(region, litExit.x, litExit.y);
  const cropped = !fullMap && region !== null && !litOutside;
  const v = cropped
    ? {
      x0: Math.max(0, region.x0 - REGION_MARGIN), x1: Math.min(W, region.x1 + REGION_MARGIN),
      y0: Math.max(0, region.y0 - REGION_MARGIN), y1: Math.min(H, region.y1 + REGION_MARGIN),
    }
    : { x0: 0, x1: W, y0: 0, y1: H };
  const vw = v.x1 - v.x0;
  const vh = v.y1 - v.y0;
  const inView = (p: { x: number; y: number }) => inside(v, p.x, p.y);
  const at = (x: number, y: number): CSSProperties => ({
    left: `${((x - v.x0) / vw) * 100}%`,
    top: `${((v.y1 - y) / vh) * 100}%`,
  });
  // Keep a label inside the view near the left / right edge...
  const side = (x: number) => {
    const f = (x - v.x0) / vw;
    return f < 0.2 ? 'start' : f > 0.8 ? 'end' : undefined;
  };
  // ...and above the point near the bottom edge.
  const up = (y: number) => ((v.y1 - y) / vh > 0.9 ? true : undefined);
  const warps = data.exits.filter(inView);
  const npcs = data.npcs.filter(inView);
  const spawns = data.spawns.filter(inView);
  const hlWarp = (w: MinimapExit) => litExit !== null && w.key === litExit.key;
  const hlSpawn = (sp: (typeof spawns)[number]) =>
    highlight !== null && 'npcId' in highlight && sp.npc_id === highlight.npcId;
  // Click the map: plan a walk from the player's tile to the clicked tile.
  // Preview only; nothing is sent to the game.
  const pickTarget = (e: MouseEvent<HTMLDivElement>) => {
    if (!placed || stageId === null || walking) return;
    const box = e.currentTarget.getBoundingClientRect();
    const gx = v.x0 + ((e.clientX - box.left) / box.width) * vw;
    const gy = v.y1 - ((e.clientY - box.top) / box.height) * vh;
    const tx = Math.floor(gx / data.tile_px);
    const ty = Math.floor(gy / data.tile_px);
    if (tx === position.x && ty === position.y) return; // clicked the player itself
    const req = ++routeReq.current;
    setWalk(null);
    setRoute({ tx, ty, plan: null, failed: false });
    get<WalkPlan>(`/api/maps/${stageId}/walk?x=${position.x}&y=${position.y}&tx=${tx}&ty=${ty}`)
      .then((plan) => { if (routeReq.current === req) setRoute({ tx, ty, plan, failed: false }); })
      .catch(() => { if (routeReq.current === req) setRoute({ tx, ty, plan: null, failed: true }); });
  };
  const plan = route?.plan ?? null;
  const clearRoute = () => { routeReq.current++; setRoute(null); setWalk(null); };
  const startWalk = () => {
    if (!plan?.goal) return;
    post<WalkStatus>(`/api/characters/${pid}/walk`, { x: plan.goal.x, y: plan.goal.y })
      .then(setWalk)
      .catch(() => setWalk({ state: 'failed', legs: 0, message: '無法開始走路' }));
  };
  const stopWalk = () => {
    post<WalkStatus>(`/api/characters/${pid}/walk/stop`).then(setWalk).catch(() => { /* poll will tell */ });
  };
  const canWalk = plan !== null && plan.reason == null && plan.hops.length > 0 && !walking;
  const walkNote = walk === null || walk.state === 'idle' ? null
    : walk.state === 'walking' ? `走路中… 第 ${walk.legs} 段`
      : walk.state === 'done' ? '已抵達'
        : walk.message?.includes('could not end the drag')
          ? WALK_FAILURES['could not end the drag: follow-the-cursor mode may still be on']
          : WALK_FAILURES[walk.message ?? ''] ?? walk.message ?? '已停止';
  const walkBad = walk?.state === 'failed';
  const routeNote = route === null ? null
    : route.failed ? '路線規劃失敗'
      : plan === null ? '規劃中…'
        : plan.reason !== null && plan.reason !== undefined
          ? `${plan.hops.length > 0 ? '只能走到一半：' : ''}${WALK_REASONS[plan.reason] ?? plan.reason}`
          : `${plan.hops.length} 段點擊`;
  const routeBad = route !== null && (route.failed || (plan !== null && plan.reason != null));
  const tileCentre = (p: { x: number; y: number }) => ({
    x: p.x * data.tile_px + data.tile_px / 2, y: p.y * data.tile_px + data.tile_px / 2,
  });
  const svgPt = (t: { x: number; y: number }) => {
    const c = tileCentre(t);
    return `${c.x - v.x0},${v.y1 - c.y}`;
  };
  // The planner snaps the target to walkable ground; mark where it really ends.
  const goalTile = plan?.goal ?? (route ? { x: route.tx, y: route.ty } : null);
  const isGoal = (h: { x: number; y: number }) => goalTile !== null && h.x === goalTile.x && h.y === goalTile.y;

  const counts: Record<Layer, number> = {
    warps: warps.length, npcs: npcs.length, spawns: spawns.length,
  };

  return (
    <div className="mm">
      <div className="mm-bar">
        <div className="mm-layers" role="group" aria-label="圖層">
          {LAYERS.map((l) => (
            <button
              key={l.k} type="button" className="mm-layer" data-layer={l.k}
              aria-pressed={shown[l.k]} disabled={counts[l.k] === 0}
              onClick={() => setShown((s) => ({ ...s, [l.k]: !s[l.k] }))}
            >
              <i aria-hidden />{l.n}<span className="mm-count">{counts[l.k]}</span>
            </button>
          ))}
        </div>
        {region !== null && (
          <button
            type="button" className="mm-layer mm-scope" aria-pressed={fullMap}
            onClick={() => setFullMap((f) => !f)}
          >
            全圖
          </button>
        )}
        {(route !== null || walkNote !== null) && (
          <span className="mm-route" data-bad={(walkNote !== null ? walkBad : routeBad) || undefined} role="status">
            {walkNote ?? routeNote}
            {canWalk && (
              <button type="button" className="mm-layer mm-go" onClick={startWalk}>走過去</button>
            )}
            {walking ? (
              <button type="button" className="mm-layer mm-clear" onClick={stopWalk}>停止</button>
            ) : (
              <button type="button" className="mm-layer mm-clear" onClick={clearRoute}>清除路線</button>
            )}
          </span>
        )}
        <span className="mm-coord">
          {placed ? `${position.x} , ${position.y}` : '走一步後顯示位置'}
        </span>
      </div>

      <div
        className="mm-map" data-hl={highlight !== null || undefined} data-pick={(placed && !walking) || undefined}
        title={placed ? '點地圖預覽走路路線' : undefined}
        onClick={pickTarget}
        style={{ aspectRatio: `${vw} / ${vh}`, maxWidth: `calc(var(--mm-max-h, ${MAX_HEIGHT}px) * ${vw / vh})` }}
      >
        {data.image_url && !imgBroken ? (
          <img
            src={data.image_url} alt={`${data.stage.name} 地圖`} draggable={false} decoding="async"
            style={{
              width: `${(W / vw) * 100}%`, height: `${(H / vh) * 100}%`,
              left: `${(-v.x0 / vw) * 100}%`, top: `${(-(H - v.y1) / vh) * 100}%`,
            }}
            onError={() => setImgBroken(true)}
          />
        ) : (
          <div className="mm-noimg">無地圖圖片</div>
        )}

        {spawns.map((s, i) => (shown.spawns || hlSpawn(s)) && (
          <span
            key={`s${i}`} className="mm-pt mm-spawn" data-tone={levelTone(s.level, charLevel)}
            data-lit={hlSpawn(s) || undefined}
            style={at(s.x, s.y)} tabIndex={0} role="img"
            aria-label={`${s.name ?? `#${s.npc_id}`} Lv ${s.level ?? '—'}`}
          >
            <b className="mm-tip" data-side={side(s.x)} data-up={up(s.y)}>{s.name ?? `#${s.npc_id}`} · Lv {s.level ?? '—'}</b>
          </span>
        ))}
        {shown.npcs && npcs.map((n, i) => (
          <span
            key={`n${i}`} className="mm-pt mm-npc" style={at(n.x, n.y)}
            tabIndex={0} role="img" aria-label={n.name ?? `#${n.npc_id}`}
          >
            <b className="mm-tip" data-side={side(n.x)} data-up={up(n.y)}>{n.name ?? `#${n.npc_id}`}</b>
          </span>
        ))}
        {warps.map((w, i) => (shown.warps || hlWarp(w)) && (() => {
          const names = exitNames(w);
          return (
            <span
              key={`w${i}`} className="mm-pt mm-warp" style={at(w.x, w.y)}
              data-lit={hlWarp(w) || undefined}
              tabIndex={0} role="img" aria-label={`出口：${names}`}
            >
              <b className="mm-label" data-side={side(w.x)} data-up={up(w.y)}>{names}</b>
            </span>
          );
        })())}
        {plan?.start && plan.hops.length > 0 && (
          <svg className="mm-route-line" viewBox={`0 0 ${vw} ${vh}`} preserveAspectRatio="none" aria-hidden>
            <polyline points={[plan.start, ...plan.hops].map(svgPt).join(' ')} />
          </svg>
        )}
        {plan !== null && plan.hops.filter((h) => !isGoal(h)).map((h, i) => {
          const c = tileCentre(h);
          return <span key={`h${i}`} className="mm-pt mm-hop" style={at(c.x, c.y)} aria-hidden />;
        })}
        {goalTile !== null && (() => {
          const c = tileCentre(goalTile);
          return inView(c) && (
            <span
              className="mm-pt mm-goal" data-bad={routeBad || undefined} style={at(c.x, c.y)}
              role="img" aria-label={`目標 ${goalTile.x} , ${goalTile.y}`}
            />
          );
        })()}
        {placed && (
          <PlayerDot
            fx={(position.px! - v.x0) / vw} fy={(v.y1 - position.py!) / vh}
            x={position.x} y={position.y} stageId={stageId} view={`${v.x0},${v.y0},${vw},${vh}`}
          />
        )}
      </div>
    </div>
  );
}

/**
 * The player marker. It moves a full-size track layer with transform, so the
 * slide between tile centres stays on the compositor. A teleport, a map change
 * or a re-cropped view jumps instead of sliding across the map.
 */
function PlayerDot({ fx, fy, x, y, stageId, view }: {
  fx: number; fy: number; x: number; y: number; stageId: number | null; view: string;
}) {
  const prev = useRef<{ x: number; y: number; stageId: number | null; view: string } | null>(null);
  const p = prev.current;
  const jump = p === null || p.stageId !== stageId || p.view !== view
    || Math.max(Math.abs(x - p.x), Math.abs(y - p.y)) > JUMP_TILES;
  useEffect(() => { prev.current = { x, y, stageId, view }; });
  return (
    <div
      className="mm-track" data-jump={jump || undefined}
      style={{ transform: `translate(${fx * 100}%, ${fy * 100}%)` }}
    >
      <span className="mm-pt mm-player" role="img" aria-label="角色位置" />
    </div>
  );
}
