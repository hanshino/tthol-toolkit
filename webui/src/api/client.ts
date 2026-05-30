const base = '';  // same-origin via Vite proxy or pywebview

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'content-type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json() as Promise<T>;
}

export function openWorldSocket(onFrame: (snap: unknown) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/world`);
  ws.onmessage = (e) => onFrame(JSON.parse(e.data));
  return ws;
}
