import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { Panel, StatNum } from '../../primitives';
import type { CharacterRow, MapInfo } from '../../api/types';

export function MapAnalysis({ char }: { char: CharacterRow }) {
  const [info, setInfo] = useState<MapInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mapName = char.position.map_name;
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
  return (
    <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1fr' }}>
      <Panel title={`現址 · ${info.stage.name}`}>
        <KV label="地圖編號" value={`#${info.stage.stage_id}`} />
        <KV label="角色座標" value={`${px} , ${py}`} />
        <KV label="等級" value={charLevel} />
        <KV label="出口" value={`${info.warps.length} 處`} />
        <KV label="怪物種類" value={`${info.monsters.length} 種`} />
      </Panel>

      <Panel title="出口 · 行徑">
        {info.warps.length === 0 ? (
          <Empty text="此地無對外出口" />
        ) : (
          <div style={{ display: 'grid', gap: 4 }}>
            {info.warps.map((w) => (
              <Row
                key={`${w.dst_stage_id}-${w.dst_tag ?? 0}`}
                left={w.dst_name ?? `#${w.dst_stage_id}`}
                right={`#${w.dst_stage_id}`}
              />
            ))}
          </div>
        )}
      </Panel>

      <Panel title="駐紮怪物">
        {info.monsters.length === 0 ? (
          <Empty text="此地無怪物棲息" />
        ) : (
          <div style={{ display: 'grid', gap: 4 }}>
            <MonsterHeader />
            {info.monsters.map((m) => {
              const lvDelta = (m.level ?? 0) - charLevel;
              const tone =
                Math.abs(lvDelta) <= 3 ? 'var(--tt-ok)' :
                lvDelta > 3 ? 'var(--tt-bad)' :
                'var(--tt-mute)';
              return (
                <div
                  key={m.npc_id}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '1.2fr 40px 70px 90px 60px',
                    gap: 8,
                    fontSize: 12,
                    padding: '4px 6px',
                    borderBottom: '1px solid var(--tt-line-soft)',
                  }}
                >
                  <span style={{ fontFamily: 'var(--tt-font-serif)' }}>
                    {m.name ?? `#${m.npc_id}`}
                  </span>
                  <span style={{ color: tone, fontFamily: 'var(--tt-font-mono)' }}>Lv {m.level ?? '—'}</span>
                  <span style={{ fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-dim)' }}>
                    HP {m.hp ?? '—'}
                  </span>
                  <span style={{ fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-gold)' }}>
                    {m.drop_money_min ?? 0}–{m.drop_money_max ?? 0}
                  </span>
                  <span style={{ textAlign: 'right' }}><StatNum value={m.count} /></span>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <Panel title="周遭刷新點 (距離)">
        {info.nearby.length === 0 ? (
          <Empty text="無資料" />
        ) : (
          <div style={{ display: 'grid', gap: 4 }}>
            {info.nearby.map((sp, i) => (
              <Row
                key={`${sp.npc_id}-${sp.x}-${sp.y}-${i}`}
                left={`${sp.name ?? `#${sp.npc_id}`}`}
                right={`Δ${sp.distance ?? '—'} · ${sp.x},${sp.y}`}
              />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

function KV({ label, value }: { label: string; value: string | number }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
      <span style={{ color: 'var(--tt-dim)', letterSpacing: 2, fontSize: 12 }}>{label}</span>
      <span style={{ fontFamily: 'var(--tt-font-mono)' }}>{value}</span>
    </div>
  );
}

function Row({ left, right }: { left: string; right: string }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between',
      padding: '4px 6px', borderBottom: '1px solid var(--tt-line-soft)',
      fontSize: 12,
    }}>
      <span style={{ fontFamily: 'var(--tt-font-serif)' }}>{left}</span>
      <span style={{ fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-dim)' }}>{right}</span>
    </div>
  );
}

function MonsterHeader() {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1.2fr 40px 70px 90px 60px',
      gap: 8, padding: '4px 6px',
      fontSize: 11, color: 'var(--tt-mute)', letterSpacing: 2,
      borderBottom: '1px solid var(--tt-line-soft)',
    }}>
      <span>名</span>
      <span>級</span>
      <span>氣血</span>
      <span>掉銀</span>
      <span style={{ textAlign: 'right' }}>數量</span>
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
