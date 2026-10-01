import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { describeError, reportClientError } from '../../diag/report';
import type { CharacterDetail } from '../../api/types';

// The worker re-reads stats and containers every poll (~3 s).
const POLL_MS = 3000;

export type DetailState = { detail: CharacterDetail | null; error: string | null };

/** One detail poll per workspace, shared by every tab that needs it. */
export function useCharacterDetail(pid: number, enabled: boolean): DetailState {
  const [state, setState] = useState<DetailState>({ detail: null, error: null });
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const fetchOnce = () =>
      get<CharacterDetail>(`/api/characters/${pid}`)
        .then(d => { if (alive) setState({ detail: d, error: null }); })
        .catch(e => {
          if (!alive) return;
          // Keep the last good detail on screen; only flag the failure.
          setState(s => ({ ...s, error: describeError(e) }));
          reportClientError(e, { component: 'useCharacterDetail' });
        });
    fetchOnce();
    const id = setInterval(fetchOnce, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [pid, enabled]);
  return state;
}
