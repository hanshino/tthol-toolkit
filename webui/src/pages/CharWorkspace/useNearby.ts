import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import type { Nearby, NearbyEntity } from '../../api/types';

const POLL_MS = 1000;

export type NearbyState = {
  entities: NearbyEntity[];
  /** Date.now() of the last good read; null until the first one lands. */
  at: number | null;
  failed: boolean;
};

/**
 * Players / monsters / NPCs the client holds around the character, polled
 * once a second while `enabled` (the live map or the 周遭 list is open).
 * One request at a time: the next poll waits for the previous answer.
 */
export function useNearby(pid: number, enabled: boolean): NearbyState {
  const [state, setState] = useState<NearbyState>({ entities: [], at: null, failed: false });
  useEffect(() => {
    setState({ entities: [], at: null, failed: false });
    if (!enabled) return;
    let cancelled = false;
    let timer = 0;
    const poll = () => {
      get<Nearby>(`/api/characters/${pid}/nearby`)
        .then((d) => { if (!cancelled) setState({ entities: d.entities, at: Date.now(), failed: false }); })
        .catch(() => { if (!cancelled) setState((s) => ({ ...s, failed: true })); })
        .finally(() => { if (!cancelled) timer = window.setTimeout(poll, POLL_MS); });
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [pid, enabled]);
  return state;
}
