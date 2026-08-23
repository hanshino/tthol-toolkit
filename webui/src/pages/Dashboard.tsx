import { useMemo, useState } from 'react';
import { post } from '../api/client';
import { describeError, reportClientError } from '../diag/report';
import type { CharacterRow, ConnectResult, OkResponse } from '../api/types';
import { Bar, BuffChips, LinkDot, Panel, StatNum } from '../primitives';

export function Dashboard({
  chars, onPick,
}: { chars: CharacterRow[]; onPick: (c: CharacterRow) => void }) {
  const [rescanning, setRescanning] = useState<number | null>(null);
  const [rescanError, setRescanError] = useState<string | null>(null);
  const handleRescan = async (pid: number) => {
    setRescanning(pid);
    setRescanError(null);
    try {
      await post<OkResponse>(`/api/characters/${pid}/rescan`);
    } catch (e) {
      // A failed 重偵 is the moment a user gives up and files a report, so it
      // must not vanish into a console nobody reads.
      setRescanError(describeError(e));
      reportClientError(e, { component: 'Dashboard.rescan' });
    } finally {
      setRescanning(null);
    }
  };
  const [hpDraft, setHpDraft] = useState<Record<number, string>>({});
  const [relocating, setRelocating] = useState<number | null>(null);
  const handleRelocate = async (pid: number) => {
    const hp = Number(hpDraft[pid]);
    if (!Number.isInteger(hp) || hp <= 0) {
      setRescanError('請輸入目前血量（正整數）');
      return;
    }
    setRelocating(pid);
    setRescanError(null);
    try {
      await post<ConnectResult>(`/api/characters/${pid}/relocate`, { hp });
      setHpDraft(d => ({ ...d, [pid]: '' }));
    } catch (e) {
      setRescanError(describeError(e));
      reportClientError(e, { component: 'Dashboard.relocate' });
    } finally {
      setRelocating(null);
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
        {rescanError && (
          <div role="status" style={{ color: 'var(--tt-bad)', fontSize: 12, marginBottom: 8 }}>
            重偵失敗：{rescanError}
          </div>
        )}
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
              {c.last_error && (
                <div
                  role="alert"
                  style={{
                    padding: '6px 8px', border: '1px solid var(--tt-bad)',
                    color: 'var(--tt-text)', fontSize: 11, lineHeight: 1.5,
                  }}
                >
                  <span style={{
                    color: 'var(--tt-bad)', fontFamily: 'var(--tt-font-serif)', marginRight: 6,
                  }}>錯</span>
                  {friendlyError(c.last_error)}
                  {c.last_error.code === 'E_LOCATE_EXHAUSTED' && (
                    <HpRescue
                      pid={c.pid}
                      value={hpDraft[c.pid] ?? ''}
                      busy={relocating === c.pid}
                      onChange={v => setHpDraft(d => ({ ...d, [c.pid]: v }))}
                      onSubmit={() => handleRelocate(c.pid)}
                    />
                  )}
                </div>
              )}
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


function HpRescue({
  pid, value, busy, onChange, onSubmit,
}: {
  pid: number; value: string; busy: boolean;
  onChange: (v: string) => void; onSubmit: () => void;
}) {
  const id = `hp-rescue-${pid}`;
  return (
    <div
      onClick={e => e.stopPropagation()}
      onKeyDown={e => e.stopPropagation()}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, marginTop: 8,
        paddingTop: 8, borderTop: '1px solid var(--tt-line-soft)', flexWrap: 'wrap',
      }}
    >
      <label htmlFor={id} style={{ color: 'var(--tt-dim)', letterSpacing: 1 }}>
        目前血量
      </label>
      <input
        id={id}
        type="number"
        inputMode="numeric"
        min={1}
        value={value}
        disabled={busy}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') onSubmit(); }}
        style={{
          width: 96, padding: '5px 8px', fontSize: 12,
          fontFamily: 'var(--tt-font-mono)',
          background: 'var(--tt-panel)', color: 'var(--tt-text)',
          border: '1px solid var(--tt-line-soft)',
        }}
      />
      <button
        className="is-ghost"
        onClick={onSubmit}
        disabled={busy || value.trim() === ''}
        style={{
          fontSize: 11, padding: '6px 10px', letterSpacing: 2,
          color: 'var(--tt-gold)', cursor: 'pointer',
        }}
      >
        {busy ? '定位中…' : '用血量定位'}
      </button>
      <span style={{ color: 'var(--tt-dim)', fontSize: 10 }}>
        在遊戲中查看角色目前血量，填入後即可掃描定位
      </span>
    </div>
  );
}

function friendlyError(e: NonNullable<CharacterRow['last_error']>): string {
  switch (e.code) {
    case 'E_WH_NOT_FOUND':
      return '倉庫尚未開啟 — 請先在遊戲中打開倉庫視窗';
    case 'E_INV_NOT_FOUND':
      return '找不到背包資料 — 可換張地圖後按「↻ 重偵」';
    case 'E_LOCATE_EXHAUSTED':
      // Not necessarily a login problem: when a game update invalidates the
      // pointer chain, 重偵 re-runs the same dead chain and can never succeed.
      // Manual HP is the fallback that still works, so lead with it.
      return '找不到角色 — 若已登入仍失敗，請於下方輸入目前血量定位';
    case 'E_PROC_GONE':
      return '無法連上遊戲程式 — 遊戲可能已關閉';
    case 'E_CHAIN_READ':
      return '尚未登入角色';
    default:
      return e.message;
  }
}
