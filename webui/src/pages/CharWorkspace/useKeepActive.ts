import { useCallback, useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import { reportClientError } from '../../diag/report';

type Status = { running: boolean };

/** Keep-active state for the header switch; logic moved from KeepActiveTab. */
export function useKeepActive(pid: number, enabled: boolean) {
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await get<Status>(`/api/characters/${pid}/keep-active/status`);
      setRunning(s.running);
    } catch (e) {
      // Manager may be absent off-Windows: intentionally not surfaced, but
      // still recorded so the timeline is complete.
      reportClientError(e, { component: 'useKeepActive.refresh', silent: true });
    }
  }, [pid]);

  useEffect(() => {
    if (!enabled) return;
    refresh();
    const t = window.setInterval(refresh, 2000);
    return () => window.clearInterval(t);
  }, [refresh, enabled]);

  const toggle = async () => {
    setBusy(true);
    try {
      await post(`/api/characters/${pid}/keep-active/${running ? 'stop' : 'start'}`);
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'useKeepActive.toggle' });
    } finally {
      setBusy(false);
    }
  };

  return { running, busy, toggle };
}
