import { useState } from 'react';
import { post } from '../../api/client';
import { describeError, reportClientError } from '../../diag/report';
import type { CharacterRow, ConnectResult, OkResponse } from '../../api/types';
import { friendlyError } from '../../components/friendlyError';
import { isStopped, isUnlocated } from '../../nav';
import { Bar, BuffChips, DollAvatar, LinkDot, Seal } from '../../primitives';
import { useKeepActive } from './useKeepActive';

const LOW_HP = 0.3;

function clock(ms: number) {
  return new Date(ms).toLocaleTimeString('zh-TW', { hour12: false });
}

export function CharHeader({ char, goneSince, onBackToOverview }: {
  char: CharacterRow; goneSince: number | null; onBackToOverview: () => void;
}) {
  // gone: the game process exited (pid left the snapshot), data is the last seen.
  // stopped: the worker gave up but the game still runs; 重偵 restarts it.
  const gone = goneSince !== null;
  const stopped = !gone && isStopped(char);
  const stale = gone || stopped;
  const unlocated = isUnlocated(char);
  const keep = useKeepActive(char.pid, !gone);
  const [busy, setBusy] = useState<'rescan' | 'relocate' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [hpDraft, setHpDraft] = useState('');

  const rescan = async () => {
    setBusy('rescan');
    setActionError(null);
    try {
      await post<OkResponse>(`/api/characters/${char.pid}/rescan`);
    } catch (e) {
      // A failed 重偵 is the moment a user gives up and files a report, so it
      // must not vanish into a console nobody reads.
      setActionError(`重偵失敗：${describeError(e)}`);
      reportClientError(e, { component: 'CharHeader.rescan' });
    } finally {
      setBusy(null);
    }
  };

  const relocate = async () => {
    const hp = Number(hpDraft);
    if (!Number.isInteger(hp) || hp <= 0) {
      setActionError('請輸入目前血量（正整數）');
      return;
    }
    setBusy('relocate');
    setActionError(null);
    try {
      await post<ConnectResult>(`/api/characters/${char.pid}/relocate`, { hp });
      setHpDraft('');
    } catch (e) {
      setActionError(`定位失敗：${describeError(e)}`);
      reportClientError(e, { component: 'CharHeader.relocate' });
    } finally {
      setBusy(null);
    }
  };

  const { hp, hp_max, mp, mp_max, weight, weight_max } = char.vitals;
  const low = hp_max > 0 && hp / hp_max < LOW_HP;
  const err = gone ? null : char.last_error;
  const runtime = char.autoclick.runtime_seconds;

  return (
    <section className="ws-head" aria-label="角色狀態" data-stale={stale || undefined}>
      <div className="ws-id">
        {!unlocated && char.avatar
          ? (
            <DollAvatar avatar={char.avatar} size={34} label={`${char.name} 頭像`}
              fallback={<Seal size={34}>{char.name[0]}</Seal>} />
          )
          : <Seal size={34}>{unlocated ? '?' : char.name[0]}</Seal>}
        <div className="ws-id-text">
          <div className="ws-name"><LinkDot status={gone ? 'lost' : char.link} /><span>{char.name}</span></div>
          <div className="ws-sub">
            {unlocated
              ? `pid ${char.pid} · 尚未定位`
              : `${char.sect ? `${char.sect} · ` : ''}Lv ${char.level} · pid ${char.pid}`}
          </div>
        </div>
        <div className="ws-actions">
          <button
            type="button" className="ws-toggle" aria-pressed={keep.running}
            disabled={keep.busy || gone} onClick={keep.toggle}
            title="切到別的視窗時，讓遊戲畫面持續更新"
          >
            <span className="ws-switch" aria-hidden="true"><span /></span>
            保持渲染
          </button>
          <button
            type="button" className="ws-btn" data-attn={stopped || unlocated || undefined}
            disabled={busy !== null || gone} onClick={rescan}
            title={stopped ? '重新驅動角色偵測' : '強制重新定位（資料不對時用）'}
          >
            {busy === 'rescan' ? '偵測中…' : '↻ 重偵'}
          </button>
        </div>
      </div>

      {!unlocated && (
        <>
          <div className="ws-vitals ws-dim-when-lost">
            <Vital label="氣血" tone="hp" v={hp} m={hp_max} warn={low ? '偏低' : undefined} />
            <Vital label="內力" tone="mp" v={mp} m={mp_max} />
            <Vital label="負重" tone="weight" v={weight} m={weight_max} />
            <div className="ws-vital">
              <span className="ws-vlabel">方位</span>
              <span className="ws-vnum ws-ellipsis">
                {char.position.map_name ?? '—'} · {char.position.x},{char.position.y}
              </span>
            </div>
          </div>
          <div className="ws-buffs ws-dim-when-lost">
            <BuffChips buffs={char.buffs} emptyText="目前無增益狀態" />
            {char.autoclick.running && (
              <span className="ws-auto">● 英雄培養執行中{runtime != null ? ` · ${runtime}s` : ''}</span>
            )}
          </div>
        </>
      )}

      {goneSince !== null && (
        <div className="ws-banner is-warn" role="status">
          <div>
            <span className="ws-badge">斷</span>
            遊戲程式已關閉 — 以下是 {clock(goneSince)} 的最後資料
          </div>
          <button type="button" className="ws-back" onClick={onBackToOverview}>回總覽</button>
        </div>
      )}

      {stopped && !err && (
        <div className="ws-banner is-warn" role="status">
          <div><span className="ws-badge">停</span>角色偵測已停止 — 按「↻ 重偵」重新偵測</div>
        </div>
      )}

      {err && (
        <div className="ws-banner is-bad" role="alert">
          <div><span className="ws-badge">錯</span>{friendlyError(err)}</div>
          {err.code === 'E_LOCATE_EXHAUSTED' && (
            <HpRescue
              pid={char.pid} value={hpDraft} busy={busy === 'relocate'}
              onChange={setHpDraft} onSubmit={relocate}
            />
          )}
        </div>
      )}

      {actionError && <div className="ws-banner is-bad" role="status">{actionError}</div>}
    </section>
  );
}

function Vital({ label, tone, v, m, warn }: {
  label: string; tone: 'hp' | 'mp' | 'weight'; v: number; m: number; warn?: string;
}) {
  return (
    <div className="ws-vital">
      <div className="ws-vrow">
        <span className="ws-vlabel">{label}</span>
        <span className="ws-vnum" data-warn={warn ? true : undefined}>
          {v.toLocaleString()} / {m.toLocaleString()}{warn ? ` ${warn}` : ''}
        </span>
      </div>
      <Bar value={v} max={m} tone={tone} />
    </div>
  );
}

function HpRescue({ pid, value, busy, onChange, onSubmit }: {
  pid: number; value: string; busy: boolean;
  onChange: (v: string) => void; onSubmit: () => void;
}) {
  const id = `hp-rescue-${pid}`;
  return (
    <div className="ws-rescue">
      <label htmlFor={id} style={{ color: 'var(--tt-dim)' }}>目前血量</label>
      <input
        id={id} type="number" inputMode="numeric" min={1} value={value} disabled={busy}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') onSubmit(); }}
      />
      <button type="button" className="ws-rescue-go" onClick={onSubmit} disabled={busy || value.trim() === ''}>
        {busy ? '定位中…' : '用血量定位'}
      </button>
      <span className="ws-rescue-hint">在遊戲中查看角色目前血量，填入後即可掃描定位</span>
    </div>
  );
}
