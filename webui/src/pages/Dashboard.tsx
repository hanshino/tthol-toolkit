import { useMemo, useState } from 'react';
import { post } from '../api/client';
import type { CharacterRow, OkResponse } from '../api/types';
import { Bar, BuffChips, LinkDot, Panel, StatNum } from '../primitives';

export function Dashboard({
  chars, onPick,
}: { chars: CharacterRow[]; onPick: (c: CharacterRow) => void }) {
  const [rescanning, setRescanning] = useState<number | null>(null);
  const handleRescan = async (pid: number) => {
    setRescanning(pid);
    try {
      await post<OkResponse>(`/api/characters/${pid}/rescan`);
    } catch (e) {
      console.warn('rescan failed', e);
    } finally {
      setRescanning(null);
    }
  };
  const lowHp = useMemo(
    () => chars.filter(c => c.vitals.hp_max > 0 && c.vitals.hp / c.vitals.hp_max < 0.3),
    [chars],
  );
  const autoclicking = useMemo(
    () => chars.filter(c => c.autoclick.running),
    [chars],
  );
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 16, padding: 16 }}>
      <Panel title="江湖一覽">
        <div style={{ display: 'grid', gap: 4 }}>
          <Header />
          {chars.map(c => (
            <div
              key={c.pid}
              role="button"
              tabIndex={0}
              onClick={() => onPick(c)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onPick(c); }}
              style={{
                display: 'flex', flexDirection: 'column', gap: 8, padding: 12,
                background: 'var(--tt-raised)', border: '1px solid var(--tt-line-soft)',
                color: 'var(--tt-text)', cursor: 'pointer', textAlign: 'left',
                opacity: c.link === 'lost' ? 0.65 : 1,
              }}
            >
              <div style={{
                display: 'grid',
                gridTemplateColumns: '24px minmax(0, 1fr) 40px 88px 88px 88px 72px 72px',
                gap: 10, alignItems: 'center',
              }}>
              <LinkDot status={c.link} />
              <div style={{ display: 'grid', gap: 2, minWidth: 0 }}>
                <span style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                <span style={{ color: 'var(--tt-dim)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.sect ? `${c.sect} · ` : ''}pid {c.pid}
                </span>
              </div>
              <StatNum value={c.level} />
              <VitalCell tone="hp" v={c.vitals.hp} m={c.vitals.hp_max} />
              <VitalCell tone="mp" v={c.vitals.mp} m={c.vitals.mp_max} />
              <VitalCell tone="weight" v={c.vitals.weight} m={c.vitals.weight_max} />
              <span style={{ fontSize: 11, color: 'var(--tt-mute)', fontFamily: 'var(--tt-font-mono)', display: 'grid', gap: 2, minWidth: 0 }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.position.map_name ?? '—'}</span>
                <span>{c.position.x},{c.position.y}</span>
              </span>
              <button
                className="is-ghost"
                onClick={(e) => { e.stopPropagation(); handleRescan(c.pid); }}
                disabled={rescanning === c.pid}
                title={c.link === 'lost' ? '重新驅動角色偵測' : '強制重新定位（資料不對時用）'}
                style={{
                  fontSize: 11, padding: '6px 10px', letterSpacing: 2,
                  color: c.link === 'lost' ? 'var(--tt-gold)' : 'var(--tt-mute)',
                  cursor: 'pointer',
                }}
              >
                {rescanning === c.pid ? '偵測中…' : '↻ 重偵'}
              </button>
              </div>
              <BuffChips buffs={c.buffs} />
            </div>
          ))}
        </div>
      </Panel>
      <div style={{ display: 'grid', gap: 16, gridTemplateRows: 'auto auto' }}>
        <Panel title="警示">
          {lowHp.length === 0
            ? <span style={{ color: 'var(--tt-mute)', fontSize: 12 }}>無</span>
            : lowHp.map(c => (
                <div key={c.pid} style={{ fontSize: 12, color: 'var(--tt-bad)' }}>
                  {c.name} 氣血偏低
                </div>
              ))}
        </Panel>
        <Panel title="輔助執行">
          {autoclicking.length === 0
            ? <span style={{ color: 'var(--tt-mute)', fontSize: 12 }}>未啟用</span>
            : autoclicking.map(c => (
                <div key={c.pid} style={{ fontSize: 12 }}>{c.name}</div>
              ))}
        </Panel>
      </div>
    </div>
  );
}

function Header() {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '24px minmax(0, 1fr) 40px 88px 88px 88px 72px 72px',
      gap: 10, padding: '6px 12px', fontSize: 11, color: 'var(--tt-mute)',
      letterSpacing: 2, borderBottom: '1px solid var(--tt-line-soft)',
    }}>
      <span /> <span>名 · 門派</span> <span>等級</span>
      <span>氣血</span> <span>內力</span> <span>負重</span> <span>方位</span> <span />
    </div>
  );
}

function VitalCell({ tone, v, m }: { tone: 'hp' | 'mp' | 'weight'; v: number; m: number }) {
  return (
    <div style={{ display: 'grid', gap: 2 }}>
      <Bar value={v} max={m} tone={tone} />
      <span style={{ fontSize: 10, color: 'var(--tt-mute)', fontFamily: 'var(--tt-font-mono)' }}>
        {v}/{m}
      </span>
    </div>
  );
}
