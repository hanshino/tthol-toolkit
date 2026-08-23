const base = '';  // same-origin via Vite proxy or pywebview

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;
  readonly path: string;

  constructor(path: string, status: number, detail?: string) {
    super(detail ? `${path}: ${status} — ${detail}` : `${path}: ${status}`);
    this.name = 'ApiError';
    this.path = path;
    this.status = status;
    this.detail = detail;
  }
}

// Both HTTPException and the global 500 handler reply as {"detail": ...},
// so one parse path covers every error response. Without this the UI could
// only ever show `Error: /api/xxx: 500`, which tells nobody anything.
async function fail(path: string, r: Response): Promise<never> {
  let detail: string | undefined;
  try {
    const body = await r.json();
    if (body && typeof body.detail === 'string') detail = body.detail;
  } catch { /* non-JSON body (e.g. a proxy error page) — status alone will do */ }
  throw new ApiError(path, r.status, detail);
}

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`);
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export async function put<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method: 'PUT',
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`, { method: 'DELETE' });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export async function upload<T>(path: string, file: File): Promise<T> {
  // Multipart upload; let the browser set the boundary content-type itself.
  const form = new FormData();
  form.append('file', file);
  const r = await fetch(`${base}${path}`, { method: 'POST', body: form });
  if (!r.ok) return fail(path, r);
  return r.json() as Promise<T>;
}

export function openWorldSocket(onFrame: (snap: unknown) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/world`);
  ws.onmessage = (e) => onFrame(JSON.parse(e.data));
  return ws;
}
