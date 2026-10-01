import { useEffect, useState, type CSSProperties } from 'react';
import { get } from '../../api/client';
import type { Minimap as MinimapData, MinimapRegion, Position } from '../../api/types';
import './minimap.css';

const MAX_HEIGHT = 420; // px default; CSS can lower it via --mm-max-h
const REGION_MARGIN = 40; // map px kept around the cropped space (one tile)

const inside = (r: MinimapRegion, x: number, y: number) =>
  x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;

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

/** A table row being hovered: light up its exits (by destination) or spawns (by npc). */
export type MinimapHighlight = { warpTo: number } | { npcId: number } | null;

export function Minimap({ position, charLevel, highlight = null }: {
  position: Position; charLevel: number; highlight?: MinimapHighlight;
}) {
  const stageId = position.stage_id ?? null;
  const [data, setData] = useState<MinimapData | null>(null);
  const [failed, setFailed] = useState(false);
  const [imgBroken, setImgBroken] = useState(false);
  const [shown, setShown] = useState<Record<Layer, boolean>>({ warps: true, npcs: true, spawns: true });
  const [region, setRegion] = useState<MinimapRegion | null>(null);
  const [fullMap, setFullMap] = useState(false);
  const placed = position.px != null && position.py != null;

  useEffect(() => {
    setData(null);
    setFailed(false);
    setImgBroken(false);
    if (stageId === null) return;
    let cancelled = false;
    get<MinimapData>(`/api/maps/${stageId}/minimap`)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [stageId]);

  // The space the player stands in (city vs. one of the interiors packed into
  // the same map). Only re-asked once the player leaves the current box.
  useEffect(() => { setRegion(null); }, [stageId]);
  const px = position.px ?? null;
  const py = position.py ?? null;
  const needRegion = stageId !== null && placed
    && (region === null || !inside(region, px!, py!));
  useEffect(() => {
    if (!needRegion) return;
    let cancelled = false;
    get<MinimapRegion | null>(`/api/maps/${stageId}/region?x=${position.x}&y=${position.y}`)
      .then((r) => { if (!cancelled && r) setRegion(r); })
      .catch(() => { /* no walk mask: stay on the full map */ });
    return () => { cancelled = true; };
  }, [needRegion, stageId, position.x, position.y]);

  if (stageId === null) return <div className="mm-empty">讀不到地圖編號，無法顯示小地圖</div>;
  if (failed) return <div className="mm-empty">此地圖沒有小地圖資料</div>;
  if (!data) return <div className="mm-empty">讀取中…</div>;
  if (!data.width_px || !data.height_px) return <div className="mm-empty">此地圖沒有尺寸資料</div>;

  const W = data.width_px;
  const H = data.height_px;
  // View box in game coordinates (y grows upward): the player's space plus a
  // margin, or the whole map.
  const cropped = !fullMap && region !== null;
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
  const warps = data.warps.filter(inView);
  const npcs = data.npcs.filter(inView);
  const spawns = data.spawns.filter(inView);
  const hlWarp = (w: (typeof warps)[number]) =>
    highlight !== null && 'warpTo' in highlight
    && w.destinations.some((d) => d.stage_id === highlight.warpTo);
  const hlSpawn = (sp: (typeof spawns)[number]) =>
    highlight !== null && 'npcId' in highlight && sp.npc_id === highlight.npcId;
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
        <span className="mm-coord">
          {placed ? `${position.x} , ${position.y}` : '走一步後顯示位置'}
        </span>
      </div>

      <div
        className="mm-map" data-hl={highlight !== null || undefined}
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
          const names = w.destinations.map((d) => d.name || `#${d.stage_id}`).join('／');
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
        {placed && (
          <span
            className="mm-pt mm-player" style={at(position.px!, position.py!)}
            role="img" aria-label="角色位置"
          />
        )}
      </div>
    </div>
  );
}
