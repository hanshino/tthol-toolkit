import type { GuardStatus, HandoffStatus, SupplyStatus } from '../../api/types';

// One run-state per 輔助 module, shared by each panel's own strip and the
// 輔助 sub-navigation so both always say the same thing.
export type RunState = { on: boolean; warn: boolean; text: string };

const idle = (text: string): RunState => ({ on: false, warn: false, text });

export function guardState(s: GuardStatus | null): RunState {
  if (!s?.running) return idle('已停止');
  return { on: true, warn: !!s.problem, text: s.problem ? '等待中' : '守護中' };
}

// services/withdraw.py HOST: a 領倉白名單 trip runs as 補給 under this host.
export const WITHDRAW_HOST = '領倉白名單';

export function withdrawState(s: SupplyStatus | null): RunState {
  if (!s?.running || s.host !== WITHDRAW_HOST) return idle('待命');
  return { on: true, warn: false, text: '領倉中' };
}

export function supplyState(s: SupplyStatus | null): RunState {
  if (!s?.running) return idle('待命');
  return { on: true, warn: false, text: s.host ? `${s.host}補給中` : '補給中' };
}

// `role` is the role picked in the panel; a running trade reports its own.
export function handoffState(s: HandoffStatus | null, role?: 'receive' | 'send' | null): RunState {
  if (!s?.running) return idle('待命');
  if (s.stopping) return { on: true, warn: true, text: '這輪存完就停' };
  return { on: true, warn: false, text: (s.role ?? role) === 'send' ? '送貨中' : '收貨中' };
}

export function heroState(running: boolean | undefined): RunState {
  return running ? { on: true, warn: false, text: '執行中' } : idle('未啟用');
}

export const stateClass = (s: RunState) => `gd-state${s.on ? ' is-on' : ''}${s.warn ? ' is-warn' : ''}`;
