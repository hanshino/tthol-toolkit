import { ApiError } from '../api/client';

const DEDUP_MS = 5_000;
const recent = new Map<string, number>();

export function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.detail ? `${err.detail} (${err.status})` : err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * Send an error to the backend so it joins the same timeline as backend events.
 * `silent` marks call sites that intentionally ignore the failure in the UI —
 * they still report, so the record is complete.
 */
export function reportClientError(
  err: unknown,
  ctx: { component?: string; silent?: boolean } = {},
): void {
  const message = describeError(err);
  const now = Date.now();
  const last = recent.get(message);
  // Dedup client-side too: a render loop must not flood the network before
  // the server-side window even sees it.
  if (last !== undefined && now - last < DEDUP_MS) return;
  recent.set(message, now);

  const payload = {
    message,
    url: location.hash || location.pathname,
    stack: err instanceof Error ? err.stack ?? null : null,
    component: ctx.component ?? null,
    ua: navigator.userAgent,
  };
  // Deliberately not awaited and never rethrows: reporting a failure must not
  // become a second failure.
  fetch('/api/diagnostics/client-error', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => { /* backend unreachable; nothing more we can do here */ });
}

export function installGlobalErrorHooks(): void {
  window.addEventListener('error', (e) => {
    reportClientError(e.error ?? e.message, { component: 'window.onerror' });
  });
  window.addEventListener('unhandledrejection', (e) => {
    reportClientError(e.reason, { component: 'unhandledrejection' });
  });
}
