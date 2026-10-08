import { useCallback, useEffect, useRef, useState } from 'react';
import { get } from '../../api/client';
import type { GrindStatus, GuardStatus, HandoffStatus, SupplyStatus } from '../../api/types';
import { reportClientError } from '../../diag/report';
import { AutoClickTab } from './AutoClickTab';
import { GrindPanel } from './GrindPanel';
import { GuardPanel } from './GuardPanel';
import { HandoffPanel } from './HandoffPanel';
import { SupplyPanel } from './SupplyPanel';
import { WithdrawPanel } from './WithdrawPanel';
import {
  grindState, guardState, handoffState, heroState, stateClass, supplyState, withdrawState, type RunState,
} from './assistState';
import './assist.css';

// 輔助 holds several long modules; each gets its own sub-tab so only one is on
// screen (and polling) at a time. Wide: a side menu with every module's state;
// narrow: the same buttons as a row above the content (assist.css).
type Sub = 'guard' | 'grind' | 'supply' | 'withdraw' | 'handoff' | 'hero';
const SUBS: { k: Sub; n: string; s: string }[] = [
  { k: 'guard', n: '守護', s: '補水 · buff 維持' },
  { k: 'grind', n: '打怪', s: '定點清附近的怪' },
  { k: 'supply', n: '補給', s: '賣 · 存倉 · 買' },
  { k: 'withdraw', n: '領倉', s: '白名單整疊領出' },
  { k: 'handoff', n: '分身交貨', s: '跨帳號交易' },
  { k: 'hero', n: '英雄培養', s: '自動點商人' },
];
const PREFS_KEY = 'tthol.assist.sub';
const STATUS_MS = 3000;

function loadSub(): Sub {
  try {
    const v = localStorage.getItem(PREFS_KEY);
    return SUBS.some(s => s.k === v) ? (v as Sub) : 'guard';
  } catch {
    return 'guard';
  }
}

// Every module's run state for the menu, including the hidden ones.
function useRunStates(
  pid: number, active: boolean, withHandoff: boolean, withGrind: boolean,
): Record<Sub, RunState> {
  const [guard, setGuard] = useState<GuardStatus | null>(null);
  const [supply, setSupply] = useState<SupplyStatus | null>(null);
  const [handoff, setHandoff] = useState<HandoffStatus | null>(null);
  const [hero, setHero] = useState(false);
  const [grind, setGrind] = useState<GrindStatus | null>(null);

  const refresh = useCallback(async () => {
    const one = async <T,>(path: string, set: (v: T) => void) => {
      try {
        set(await get<T>(`/api/characters/${pid}/${path}`));
      } catch (e) {
        reportClientError(e, { component: 'AssistTab.status', silent: true });
      }
    };
    await Promise.all([
      one<GuardStatus>('guard', setGuard),
      one<SupplyStatus>('supply/status', setSupply),
      withHandoff ? one<HandoffStatus>('handoff/status', setHandoff) : null,
      withGrind ? one<GrindStatus>('grind/status', setGrind) : null,
      one<{ running: boolean }>('autoclick/status', s => setHero(s.running)),
    ]);
  }, [pid, withHandoff, withGrind]);

  useEffect(() => {
    if (!active) return;
    refresh();
    const t = window.setInterval(refresh, STATUS_MS);
    return () => window.clearInterval(t);
  }, [active, refresh]);

  return {
    guard: guardState(guard), grind: grindState(grind), supply: supplyState(supply), withdraw: withdrawState(supply),
    handoff: handoffState(handoff), hero: heroState(hero),
  };
}

export function AssistTab({ pid, active, canLove, canHandoff, canWithdraw, canGrind }: {
  pid: number; active: boolean; canLove: boolean; canHandoff: boolean; canWithdraw: boolean; canGrind: boolean;
}) {
  const [sub, setSub] = useState<Sub>(loadSub);
  // Mounted on first visit and then kept (hidden), like the main tabs, so an
  // open picker or half-typed search survives a switch.
  const visited = useRef(new Set<Sub>());
  // 分身交貨 needs the hook's trade commands; once opened it stays even if the
  // hook drops, so it does not vanish under the user.
  const subs = SUBS.filter(s => (s.k !== 'handoff' || canHandoff || visited.current.has('handoff'))
    && (s.k !== 'withdraw' || canWithdraw || visited.current.has('withdraw'))
    && (s.k !== 'grind' || canGrind || visited.current.has('grind')));
  const cur = subs.some(s => s.k === sub) ? sub : 'guard';
  visited.current.add(cur);
  const states = useRunStates(
    pid, active, subs.some(s => s.k === 'handoff'), subs.some(s => s.k === 'grind'),
  );

  const pick = (k: Sub) => {
    setSub(k);
    try { localStorage.setItem(PREFS_KEY, k); } catch { /* storage blocked */ }
  };
  const on = (k: Sub) => active && cur === k;

  return (
    <div className="as">
      <div className="as-in">
        <nav className="as-nav" role="tablist" aria-label="輔助功能">
          {subs.map(s => (
            <button
              key={s.k} type="button" role="tab" id={`as-tab-${s.k}`} aria-controls={`as-panel-${s.k}`}
              aria-selected={cur === s.k} className="as-tab" onClick={() => pick(s.k)}
            >
              <span className="as-tab-n">{s.n}</span>
              <span className={`as-tab-st ${stateClass(states[s.k])}`}><i />{states[s.k].text}</span>
              <span className="as-tab-s">{s.s}</span>
            </button>
          ))}
        </nav>
        <div className="as-body">
          {subs.filter(s => visited.current.has(s.k)).map(s => (
            <div key={s.k} role="tabpanel" id={`as-panel-${s.k}`} aria-labelledby={`as-tab-${s.k}`} hidden={cur !== s.k}>
              {s.k === 'guard' && <GuardPanel pid={pid} active={on('guard')} canLove={canLove} />}
              {s.k === 'grind' && <GrindPanel pid={pid} active={on('grind')} />}
              {s.k === 'supply' && <SupplyPanel pid={pid} active={on('supply')} />}
              {s.k === 'withdraw' && <WithdrawPanel pid={pid} active={on('withdraw')} />}
              {s.k === 'handoff' && <HandoffPanel pid={pid} active={on('handoff')} />}
              {s.k === 'hero' && <AutoClickTab pid={pid} active={on('hero')} />}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
