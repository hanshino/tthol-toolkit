import { useEffect, useState } from 'react';
import { get, openWorldSocket } from '../api/client';
import { reportClientError } from '../diag/report';
import type { WorldSnapshot } from '../api/types';

export function useLiveChars(): WorldSnapshot {
  const [snap, setSnap] = useState<WorldSnapshot>({ chars: [], server_ts: 0 });

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let backoff = 1000;

    const connect = async () => {
      try {
        const initial = await get<WorldSnapshot>('/api/world');
        if (!cancelled) setSnap(initial);
      } catch (e) {
        // The WS reconnect loop below covers the UX; record it so a "nothing
        // loads" report has a first cause in the timeline. A console.warn is no
        // better than swallowing it: the WebView2 console is invisible to the
        // user and absent from the bundle.
        reportClientError(e, { component: 'useLiveChars', silent: true });
      }

      ws = openWorldSocket((frame) => setSnap(frame as WorldSnapshot));
      ws.onclose = () => {
        if (cancelled) return;
        setTimeout(() => { if (!cancelled) connect(); }, backoff);
        backoff = Math.min(backoff * 2, 30_000);
      };
      ws.onopen = () => { backoff = 1000; };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => { cancelled = true; ws?.close(); };
  }, []);

  return snap;
}
