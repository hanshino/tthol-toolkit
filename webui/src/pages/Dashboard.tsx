import type { CharacterRow } from '../api/types';
import { friendlyError } from '../components/friendlyError';
import { isStopped, isUnlocated, type OpenChar } from '../nav';
import { Bar, BuffChips, LinkDot, Panel, StatNum } from '../primitives';

// Name and bar columns flex so names stop truncating; recovery actions
// (重偵 / 用血量定位) live in the character workspace header now.
const COLS = '24px minmax(0, 1.4fr) 40px repeat(3, minmax(0, 1fr)) minmax(0, 1fr)';
const LOW_HP = 0.3;

type Alert = { pid: number; text: string; tone: 'bad' | 'warn' };

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
  }
  return out;
}

export function Dashboard({ chars, onOpenChar }: { chars: CharacterRow[]; onOpenChar: OpenChar }) {
  const alerts = alertsFor(chars);
  const autoclicking = chars.filter(c => c.autoclick.running);
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

      <Panel title="總覽">
        {chars.length === 0
          ? <div style={{ color: 'var(--tt-dim)', fontSize: 12, padding: 24, textAlign: 'center' }}>尚未偵測到遊戲程式 — 開啟遊戲後會自動出現在這裡</div>
          : (
            <div style={{ display: 'grid', gap: 4 }}>
              <Header />
              {chars.map(c => <Row key={c.pid} c={c} onOpen={() => onOpenChar(c.pid)} />)}
            </div>
          )}
      </Panel>
    </div>
  );
}

function Row({ c, onOpen }: { c: CharacterRow; onOpen: () => void }) {
  const unlocated = isUnlocated(c);
  return (
    <button
      type="button" onClick={onOpen}
      style={{
        display: 'flex', flexDirection: 'column', gap: 8, padding: 12, width: '100%',
        background: 'var(--tt-raised)', border: '1px solid var(--tt-line-soft)',
        color: 'var(--tt-text)', textAlign: 'left', letterSpacing: 0,
        opacity: isStopped(c) ? 0.65 : 1,
      }}
    >
      <span style={{ display: 'grid', gridTemplateColumns: COLS, gap: 10, alignItems: 'center', width: '100%' }}>
        <LinkDot status={c.link} />
        <span style={{ display: 'grid', gap: 2, minWidth: 0 }}>
          <span style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
          <span style={{ color: 'var(--tt-dim)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {c.sect ? `${c.sect} · ` : ''}pid {c.pid}
          </span>
        </span>
        {unlocated ? <span style={{ color: 'var(--tt-dim)' }}>—</span> : <StatNum value={c.level} />}
        <VitalCell tone="hp" v={c.vitals.hp} m={c.vitals.hp_max} />
        <VitalCell tone="mp" v={c.vitals.mp} m={c.vitals.mp_max} />
        <VitalCell tone="weight" v={c.vitals.weight} m={c.vitals.weight_max} />
        <span style={{ fontSize: 11, color: 'var(--tt-dim)', fontFamily: 'var(--tt-font-mono)', display: 'grid', gap: 2, minWidth: 0 }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.position.map_name ?? '—'}</span>
          <span>{unlocated ? '' : `${c.position.x},${c.position.y}`}</span>
        </span>
      </span>
      <BuffChips buffs={c.buffs} />
      {c.last_error && (
        <span style={{ fontSize: 11, color: 'var(--tt-bad)', lineHeight: 1.5 }}>
          <span style={{ fontFamily: 'var(--tt-font-serif)', marginRight: 6 }}>錯</span>
          {friendlyError(c.last_error)} — 點進去處理
        </span>
      )}
    </button>
  );
}

function Header() {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: COLS, gap: 10, padding: '6px 12px',
      fontSize: 11, color: 'var(--tt-dim)', letterSpacing: 2, borderBottom: '1px solid var(--tt-line-soft)',
    }}>
      <span /> <span>名 · 門派</span> <span>等級</span>
      <span>氣血</span> <span>內力</span> <span>負重</span> <span>方位</span>
    </div>
  );
}

function VitalCell({ tone, v, m }: { tone: 'hp' | 'mp' | 'weight'; v: number; m: number }) {
  return (
    <span style={{ display: 'grid', gap: 2, minWidth: 0 }}>
      <Bar value={v} max={m} tone={tone} />
      <span style={{ fontSize: 10, color: 'var(--tt-dim)', fontFamily: 'var(--tt-font-mono)' }}>{v}/{m}</span>
    </span>
  );
}
