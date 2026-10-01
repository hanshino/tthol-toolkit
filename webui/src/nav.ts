import type { CharacterRow } from './api/types';

export type CharTab = 'items' | 'body' | 'maps' | 'assist';
export type GlobalView = 'overview' | 'treasury' | 'snapshots' | 'diagnostics';
export type View = { kind: GlobalView } | { kind: 'char'; pid: number };
export type OpenChar = (pid: number, tab?: CharTab) => void;

/**
 * The worker emits a placeholder row (name "(連線中)", level 0, empty vitals)
 * until a character has located once; see worker_manager._placeholder_row.
 */
export function isUnlocated(c: CharacterRow): boolean {
  return c.level === 0 && c.vitals.hp_max === 0;
}

/**
 * link 'lost' means the worker stopped (locate retries exhausted, connect or
 * read failure) while the game process is still running; 重偵 restarts it.
 * A closed game is NOT 'lost': WorkerManager.world_snapshot drops its row, so
 * "gone" is detected by the pid vanishing from the snapshot (see App.tsx).
 */
export function isStopped(c: CharacterRow): boolean {
  return c.link === 'lost';
}

/**
 * Treasury and snapshot rows carry a character name, not a pid. Link only when
 * exactly one live character has that name: two accounts can share a name and
 * a wrong jump is worse than no jump.
 */
export function pidForName(chars: CharacterRow[], name: string): number | null {
  const hits = chars.filter(c => c.name === name);
  return hits.length === 1 ? hits[0].pid : null;
}
