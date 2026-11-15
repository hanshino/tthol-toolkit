import type { CharacterRow } from '../api/types';
import { friendlyError } from '../components/friendlyError';
import { isStopped, isUnlocated, type OpenChar } from '../nav';
import { Bar, LinkDot, Panel } from '../primitives';
import './dashboard.css';

// 總覽: alerts on top, then a card per character.
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
                fontSize: 12, padding: '4px 10px',
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

      {chars.length === 0
        ? (
          <Panel title="總覽">
            <div style={{ color: 'var(--tt-dim)', fontSize: 12, padding: 24, textAlign: 'center' }}>尚未偵測到遊戲程式，開啟遊戲後會自動出現在這裡</div>
          </Panel>
        )
        : (
          <section className="db-sec">
            <header className="db-title">總覽</header>
            <div className="db-board">
              {chars.map(c => (
                <Card key={c.pid} c={c} onOpen={() => onOpenChar(c.pid)} />
              ))}
            </div>
          </section>
        )}
    </div>
  );
}

function Card({ c, onOpen }: { c: CharacterRow; onOpen: () => void }) {
  const unlocated = isUnlocated(c);
  const sub = unlocated
    ? `pid ${c.pid}`
    : [`Lv ${c.level}`, c.sect, c.position.map_name].filter(Boolean).join('　');
  return (
    <div className={`db-card${isStopped(c) ? ' is-lost' : ''}`}>
      <button type="button" className="db-nm" onClick={onOpen} title="開啟角色">
        <b><LinkDot status={c.link} size={7} />{c.name}</b>
        <span>{sub}</span>
      </button>
      {unlocated
        ? <div className="db-step">角色還沒定位</div>
        : (
          <div className="db-vitals">
            <Vital tone="hp" v={c.vitals.hp} m={c.vitals.hp_max} />
            <Vital tone="mp" v={c.vitals.mp} m={c.vitals.mp_max} />
          </div>
        )}
      {c.last_error && (
        <span className="db-err">
          <span style={{ fontFamily: 'var(--tt-font-serif)', marginRight: 6 }}>錯</span>
          {friendlyError(c.last_error)}，點名字進去處理
        </span>
      )}
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
