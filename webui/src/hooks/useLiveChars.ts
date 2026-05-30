import { useEffect, useState } from 'react';
import { get, openWorldSocket } from '../api/client';
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
        console.warn('initial /api/world failed', e);
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
