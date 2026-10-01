import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { useLivePosition } from '../../api/positionStore';
import { Panel, StatNum } from '../../primitives';
import type { CharacterRow, MapInfo } from '../../api/types';
import { exitNames, levelTone, Minimap, useMinimapData, type MinimapHighlight } from './Minimap';

type ListTab = 'warps' | 'monsters' | 'nearby';

// Map on the left (fixed while the list scrolls), one list at a time on the
// right. Hovering a row lights its points on the map.
export function MapAnalysis({ char }: { char: CharacterRow }) {
  const [info, setInfo] = useState<MapInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<ListTab>('warps');
  const [hl, setHl] = useState<MinimapHighlight>(null);
  // The fast stream moves the map and the dot; the lists below follow the
  // 3 s row, so a step does not refetch them.
  const pos = useLivePosition(char.pid) ?? char.position;
  const mapName = char.position.map_name;
  const minimap = useMinimapData(pos.stage_id ?? null);
  // Walk-on exits from the map's own scripts (genbu getPortalExits); MapInfo's
  // warp list also carries the legacy byte-scan guesses and NPC dialogue warps.
  const exits = minimap.data?.exits ?? [];
  const px = char.position.x;
  const py = char.position.y;

  useEffect(() => {
    if (!mapName) { setInfo(null); return; }
    let cancelled = false;
    setError(null);
    const url = `/api/maps/by-name/${encodeURIComponent(mapName)}?x=${px}&y=${py}`;
    get<MapInfo>(url)
      .then((d) => { if (!cancelled) setInfo(d); })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          setInfo(null);
        }
      });
    return () => { cancelled = true; };
  }, [mapName, px, py]);

  if (!mapName) {
    return <Panel title="行止"><Empty text="尚未取得地圖位置" /></Panel>;
  }
  if (error) {
    return <Panel title="行止"><Empty text={`地圖資料查無：${mapName}`} /></Panel>;
  }
  if (!info) {
    return <Panel title="行止"><Empty text="讀取中…" /></Panel>;
  }

  const charLevel = char.level ?? 0;
  const tabs: { k: ListTab; n: string; count: number }[] = [
    { k: 'warps', n: '出口', count: exits.length },
    { k: 'monsters', n: '怪物', count: info.monsters.length },
    { k: 'nearby', n: '刷新點', count: info.nearby.length },
  ];
  // Spread onto a row: hover and keyboard focus both light the map.
  const lights = (h: MinimapHighlight) => ({
    tabIndex: 0,
    onMouseEnter: () => setHl(h), onMouseLeave: () => setHl(null),
    onFocus: () => setHl(h), onBlur: () => setHl(null),
  });

  return (
    <div className="ma">
      <div className="ma-map">
        <Panel title={`輿圖 · ${pos.map_name ?? info.stage.name}　#${pos.stage_id ?? info.stage.stage_id}`}>
          <Minimap
            position={pos} charLevel={charLevel} highlight={hl}
            data={minimap.data} failed={minimap.failed}
          />
        </Panel>
      </div>

      <Panel style={{ minWidth: 0 }}>
        <div className="ma-tabs" role="tablist" aria-label="地圖資料">
          {tabs.map((t) => (
            <button
              key={t.k} type="button" role="tab" className="ma-tab"
              aria-selected={tab === t.k} onClick={() => { setTab(t.k); setHl(null); }}
            >
              {t.n}<span className="ma-count">{t.count}</span>
            </button>
          ))}
        </div>

        <div role="tabpanel" className="ma-list">
          {tab === 'warps' && (exits.length === 0 ? <Empty text="此地無踩點出口" /> : (
            exits.map((e) => {
              const menu = e.options.length > 1 || e.prompt !== null;
              const single = e.options.length === 1 ? e.options[0] : null;
              return (
                <div
                  key={e.key} className="ma-row" {...lights({ exitKey: e.key })}
                  title={e.prompt ?? undefined}
                >
                  <span className="ma-name">
                    {exitNames(e)}
                    {e.parts > 1 && <span className="ma-dim">（{e.part}／{e.parts}）</span>}
                  </span>
                  <span className="ma-tags">
                    {menu && <span className="ma-tag">對話選單</span>}
                    {e.options.some((o) => o.instance) && <span className="ma-tag">副本</span>}
                    {single && <span className="ma-mono ma-dim">#{single.stage_id}</span>}
                  </span>
                </div>
              );
            })
          ))}

          {tab === 'monsters' && (info.monsters.length === 0 ? <Empty text="此地無怪物棲息" /> : (
            <>
              <div className="ma-row ma-mon ma-head">
                <span>名</span><span>級</span><span>氣血</span><span>掉銀</span>
                <span style={{ textAlign: 'right' }}>數量</span>
              </div>
              {info.monsters.map((m) => (
                <div key={m.npc_id} className="ma-row ma-mon" {...lights({ npcId: m.npc_id })}>
                  <span className="ma-name">{m.name ?? `#${m.npc_id}`}</span>
                  <span className="ma-mono" data-tone={levelTone(m.level, charLevel)}>Lv {m.level ?? '—'}</span>
                  <span className="ma-mono ma-dim">HP {m.hp ?? '—'}</span>
                  <span className="ma-mono ma-gold">{m.drop_money_min ?? 0}–{m.drop_money_max ?? 0}</span>
                  <span style={{ textAlign: 'right' }}><StatNum value={m.count} /></span>
                </div>
              ))}
            </>
          ))}

          {tab === 'nearby' && (info.nearby.length === 0 ? <Empty text="無資料" /> : (
            info.nearby.map((sp, i) => (
              <div
                key={`${sp.npc_id}-${sp.x}-${sp.y}-${i}`} className="ma-row"
                {...lights({ npcId: sp.npc_id })}
              >
                <span className="ma-name">{sp.name ?? `#${sp.npc_id}`}</span>
                <span className="ma-mono ma-dim">{sp.distance ?? '—'} 格 · {sp.x},{sp.y}</span>
              </div>
            ))
          ))}
        </div>
      </Panel>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div style={{ color: 'var(--tt-mute)', fontSize: 12, padding: 24, textAlign: 'center' }}>
      {text}
    </div>
  );
}
