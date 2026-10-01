import { useCallback, useSyncExternalStore } from 'react';
import { openSocket } from './client';
import type { Position, PositionFrame } from './types';

// Fast player positions from /ws/pos, kept outside React so a frame only
// re-renders the components subscribed to the pids it carries. One socket for
// all characters, opened on the first subscriber and closed after the last.

const positions = new Map<number, Position>();
const listeners = new Map<number, Set<() => void>>();
let ws: WebSocket | null = null;
let subscribers = 0;
let backoff = 1000;
let retry: number | undefined;
let idleClose: number | undefined;
// Close the socket only after nobody has listened for this long, so a remount
// or a tab switch does not drop and reopen it.
const IDLE_CLOSE_MS = 5000;

function notify(pid: number) {
  listeners.get(pid)?.forEach((cb) => cb());
}

function connect() {
  const sock = openSocket('/ws/pos', (frame) => {
    for (const [pid, pos] of Object.entries((frame as PositionFrame).pos)) {
      positions.set(Number(pid), pos);
      notify(Number(pid));
    }
  });
  ws = sock;
  sock.onopen = () => { backoff = 1000; };
  sock.onerror = () => sock.close();
  sock.onclose = () => {
    if (ws === sock) ws = null;
    // Drop what we had so callers fall back to the slow world row instead of
    // showing a frozen dot while the socket is down.
    const stale = [...positions.keys()];
    positions.clear();
    stale.forEach(notify);
    if (subscribers > 0 && retry === undefined) {
      retry = window.setTimeout(() => { retry = undefined; if (subscribers > 0) connect(); }, backoff);
      backoff = Math.min(backoff * 2, 30_000);
    }
  };
}

function subscribe(pid: number, cb: () => void) {
  let set = listeners.get(pid);
  if (!set) listeners.set(pid, (set = new Set()));
  set.add(cb);
  subscribers += 1;
  window.clearTimeout(idleClose);
  idleClose = undefined;
  if (ws === null && retry === undefined) connect();
  return () => {
    set.delete(cb);
    subscribers -= 1;
    if (subscribers === 0) {
      idleClose = window.setTimeout(() => {
        idleClose = undefined;
        if (subscribers > 0) return;
        window.clearTimeout(retry);
        retry = undefined;
        ws?.close();
      }, IDLE_CLOSE_MS);
    }
  };
}

/** The pid's latest fast position, or null until the stream has one. */
export function useLivePosition(pid: number): Position | null {
  // Stable per pid: a new function each render would make React unsubscribe
  // and resubscribe on every render.
  const sub = useCallback((cb: () => void) => subscribe(pid, cb), [pid]);
  return useSyncExternalStore(sub, () => positions.get(pid) ?? null);
}
