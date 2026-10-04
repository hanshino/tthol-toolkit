import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { useLivePosition } from '../../api/positionStore';
import { Panel, StatNum } from '../../primitives';
import type { CharacterRow, MapInfo } from '../../api/types';
import { exitNames, levelTone, Minimap, useMinimapData, type MapMode, type MinimapHighlight } from './Minimap';
import { useNearby } from './useNearby';

// 出口 / 怪物 / 刷新點 read the map's data; 周遭 is what the client holds right now.
type ListTab = 'warps' | 'monsters' | 'spawns' | 'live';

// Map on the left (fixed while the list scrolls), one list at a time on the
// right. Hovering a row lights its points on the map.
export function MapAnalysis({ char, active = true }: { char: CharacterRow; active?: boolean }) {
  const [info, setInfo] = useState<MapInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<ListTab>('warps');
  const [hl, setHl] = useState<MinimapHighlight>(null);
  // The list tab and the map mode move together: 周遭 shows the live map,
  // the data tabs the data map; the map's own switch moves the tab back.
  const [mode, setMode] = useState<MapMode>('data');
  // Polled only while the live view is open on a visible 行止 tab (hidden tabs stay mounted).
  const nearby = useNearby(char.pid, mode === 'live' && active);
  const pickTab = (k: ListTab) => { setTab(k); setHl(null); setMode(k === 'live' ? 'live' : 'data'); };
  const pickMode = (m: MapMode) => {
    setMode(m);
    setHl(null);
    if (m === 'live') setTab('live');
    else if (tab === 'live') setTab('warps');
  };
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
    { k: 'spawns', n: '刷新點', count: info.nearby.length },
  ];
  // NPCs stand where the data layer already draws them, so 周遭 leaves them out.
  const livePlayers = nearby.entities.filter((e) => e.kind === 'player');
  const liveFollowers = nearby.entities.filter((e) => e.kind === 'follower');
  const liveMonsters = nearby.entities.filter((e) => e.kind === 'monster');
  const liveCount = livePlayers.length + liveFollowers.length + liveMonsters.length;
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
            pid={char.pid}
            position={pos} charLevel={charLevel} highlight={hl}
            data={minimap.data} failed={minimap.failed}
            mode={mode} onMode={pickMode} nearby={nearby}
          />
        </Panel>
      </div>

      <Panel style={{ minWidth: 0 }}>
        <div className="ma-tabs" role="tablist" aria-label="地圖資料">
          {tabs.map((t) => (
            <button
              key={t.k} type="button" role="tab" className="ma-tab"
              aria-selected={tab === t.k} onClick={() => pickTab(t.k)}
            >
              {t.n}<span className="ma-count">{t.count}</span>
            </button>
          ))}
          <span className="ma-tab-sep" aria-hidden />
          <button
            type="button" role="tab" className="ma-tab" aria-selected={tab === 'live'} onClick={() => pickTab('live')}
          >
            <i className="ma-live-dot" aria-hidden />周遭
            {nearby.at !== null && <span className="ma-count">{liveCount}</span>}
          </button>
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

          {tab === 'spawns' && (info.nearby.length === 0 ? <Empty text="無資料" /> : (
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

          {tab === 'live' && (
            nearby.failed && nearby.at === null ? <Empty text="讀不到周遭：角色尚未定位" />
              : nearby.at === null ? <Empty text="讀取中…" />
                : liveCount === 0 ? <Empty text="附近沒有其他玩家或怪物" />
                  : (
                    <>
                      <div className="ma-row ma-live ma-head">
                        <span /><span>名</span><span>級</span><span>氣血</span>
                        <span style={{ textAlign: 'right' }}>距離</span><span style={{ textAlign: 'right' }}>座標</span>
                      </div>
                      {livePlayers.length > 0 && <div className="ma-group" data-kind="player">玩家 <span className="ma-count">{livePlayers.length}</span></div>}
                      {livePlayers.map((e) => (
                        <div key={e.handle} className="ma-row ma-live" {...lights({ handle: e.handle })}>
                          <i className="ma-mark" data-kind="player" aria-hidden />
                          <span className="ma-name">
                            {e.name ?? '玩家'}
                            {e.family && <span className="ma-sub">{e.family}</span>}
                            {e.stalling && <span className="ma-tag ma-stall">擺攤</span>}
                          </span>
                          <span className="ma-mono ma-dim">—</span>
                          <span className="ma-mono ma-dim">—</span>
                          <span className="ma-mono ma-dim" style={{ textAlign: 'right' }}>{e.distance ?? '—'} 格</span>
                          <span className="ma-mono ma-dim" style={{ textAlign: 'right' }}>{e.x},{e.y}</span>
                        </div>
                      ))}
                      {liveFollowers.length > 0 && <div className="ma-group" data-kind="follower">跟隨 <span className="ma-count">{liveFollowers.length}</span></div>}
                      {liveFollowers.map((e) => (
                        <div key={e.handle} className="ma-row ma-live" {...lights({ handle: e.handle })}>
                          <i className="ma-mark" data-kind="follower" aria-hidden />
                          <span className="ma-name">
                            {e.name ?? `#${e.npc_id}`}
                            {e.owner && <span className="ma-sub">{e.owner} 的跟隨</span>}
                          </span>
                          <span className="ma-mono ma-dim">Lv {e.level ?? '—'}</span>
                          <span className="ma-mono ma-dim">—</span>
                          <span className="ma-mono ma-dim" style={{ textAlign: 'right' }}>{e.distance ?? '—'} 格</span>
                          <span className="ma-mono ma-dim" style={{ textAlign: 'right' }}>{e.x},{e.y}</span>
                        </div>
                      ))}
                      {liveMonsters.length > 0 && <div className="ma-group" data-kind="monster">怪物 <span className="ma-count">{liveMonsters.length}</span></div>}
                      {liveMonsters.map((e) => (
                        <div key={e.handle} className="ma-row ma-live" {...lights({ handle: e.handle })}>
                          <i className="ma-mark" data-kind="monster" data-tone={levelTone(e.level, charLevel)} aria-hidden />
                          <span className="ma-name">{e.name ?? `#${e.npc_id}`}</span>
                          <span className="ma-mono" data-tone={levelTone(e.level, charLevel)}>Lv {e.level ?? '—'}</span>
                          <span className="ma-hp" title={`${e.hp_pct ?? 0}%`}>
                            <span className="ma-hp-bar"><span style={{ width: `${e.hp_pct ?? 0}%` }} /></span>
                            <span className="ma-mono ma-dim">{e.hp_pct ?? '—'}%</span>
                          </span>
                          <span className="ma-mono ma-dim" style={{ textAlign: 'right' }}>{e.distance ?? '—'} 格</span>
                          <span className="ma-mono ma-dim" style={{ textAlign: 'right' }}>{e.x},{e.y}</span>
                        </div>
                      ))}
                      <p className="ma-note">只列遊戲畫面附近載入的物件，離開視野就會消失。</p>
                    </>
                  )
          )}
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
