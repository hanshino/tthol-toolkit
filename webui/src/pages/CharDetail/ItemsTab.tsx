import { useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import { describeError, reportClientError } from '../../diag/report';
import type { CharacterDetail, Item, SaveSnapshotResult } from '../../api/types';
import { LinkDot, Panel, type LinkStatus } from '../../primitives';

type Source = Item['source'];

// The worker re-reads the bag every poll (~3 s); older than this means it stalled.
const STALE_MS = 10_000;

function clock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
}

function StatusLine({ label, status, text }: { label: string; status: LinkStatus; text: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--tt-dim)' }}>
      <LinkDot status={status} />
      <span style={{ color: 'var(--tt-text)', minWidth: 28 }}>{label}</span>
      <span>{text}</span>
    </div>
  );
}

export function ItemsTab({ pid }: { pid: number }) {
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [filter, setFilter] = useState<Source | 'all'>('all');
  const [saving, setSaving] = useState<Source | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = () => {
      get<CharacterDetail>(`/api/characters/${pid}`)
        .then(d => { if (!cancelled) setDetail(d); })
        .catch(e => {
          if (cancelled) return;
          setToast(`讀取失敗：${describeError(e)}`);
          reportClientError(e, { component: 'ItemsTab' });
        });
    };
    fetchOnce();
    const id = setInterval(fetchOnce, 3000); // bag + warehouse are re-read by the worker every poll
    return () => { cancelled = true; clearInterval(id); };
  }, [pid]);

  const saveSnapshot = async (source: Source) => {
    if (saving) return;
    setSaving(source);
    setToast(null);
    try {
      const r = await post<SaveSnapshotResult>('/api/snapshots', { pid, source });
      setToast(r.saved ? '已存入留影' : '無新內容可存（可能與最近一筆相同）');
    } catch (e) {
      setToast(`保存失敗：${describeError(e)}`);
      reportClientError(e, { component: 'ItemsTab.saveSnapshot' });
    } finally {
      setSaving(null);
    }
  };

  const inventory = detail?.inventory ?? [];
  const warehouse = detail?.warehouse ?? [];
  const items = [...inventory, ...warehouse];
  const visible = items.filter(i => filter === 'all' || i.source === filter);

  const invTs = detail?.inventory_updated_at ?? null;
  const invFresh = invTs !== null && Date.now() - invTs * 1000 < STALE_MS;
  const invStatus: LinkStatus = invFresh ? 'ok' : 'weak';
  const invText = invTs === null
    ? '等待角色定位後自動讀取…'
    : invFresh ? `自動更新中 · ${clock(invTs)}` : `暫停更新 · 最後讀取 ${clock(invTs)}`;

  const whTs = detail?.warehouse_updated_at ?? null;
  const whOpen = detail?.warehouse_open ?? false;
  const whStatus: LinkStatus = whOpen ? 'ok' : 'weak';
  const whText = whOpen
    ? `倉庫開啟中 · 自動更新${whTs !== null ? ` · ${clock(whTs)}` : ''}`
    : whTs === null
      ? '尚未讀取 — 在遊戲中開啟倉庫即可自動讀取'
      : `倉庫已關閉 · 顯示 ${clock(whTs)} 的內容`;

  return (
    <Panel title="行囊 / 庫房">
      <div style={{ display: 'grid', gap: 4, marginBottom: 12 }}>
        <StatusLine label="行囊" status={invStatus} text={invText} />
        <StatusLine label="庫房" status={whStatus} text={whText} />
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <button
          className="is-ghost"
          onClick={() => saveSnapshot('inventory')}
          disabled={inventory.length === 0 || saving !== null}
          title="將目前行囊內容存入留影"
        >
          {saving === 'inventory' ? '保存中…' : '↧ 留影身'}
        </button>
        <button
          className="is-ghost"
          onClick={() => saveSnapshot('warehouse')}
          disabled={warehouse.length === 0 || saving !== null}
          title="將目前庫房內容存入留影"
        >
          {saving === 'warehouse' ? '保存中…' : '↧ 留影庫'}
        </button>
        <span style={{ flex: 1 }} />
        {(['all', 'inventory', 'warehouse'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={filter === f ? 'is-active' : ''}
          >
            {f === 'all' ? '全部' : f === 'inventory' ? '身' : '庫'}
          </button>
        ))}
      </div>
      {toast && (
        <div style={{
          padding: '6px 10px', marginBottom: 8, fontSize: 12,
          background: 'var(--tt-raised)', border: '1px solid var(--tt-line-soft)',
          color: 'var(--tt-dim)',
        }}>{toast}</div>
      )}
      <div style={{ display: 'grid', gap: 4 }}>
        {visible.map((i, idx) => (
          // The same item can fill several slots (e.g. four stacks of one potion),
          // so item_id alone is not a unique key.
          <div key={`${i.source}-${idx}-${i.item_id}`} style={{ display: 'flex', justifyContent: 'space-between', padding: 6, borderBottom: '1px solid var(--tt-line-soft)' }}>
            <span>{i.name}</span>
            <span style={{ fontFamily: 'var(--tt-font-mono)', color: 'var(--tt-dim)' }}>×{i.quantity}</span>
          </div>
        ))}
        {visible.length === 0 && (
          <div style={{ color: 'var(--tt-mute)', fontSize: 12, padding: 12 }}>
            {filter === 'warehouse' || (filter === 'all' && invTs === null)
              ? '尚無資料 — 角色定位後會自動讀取行囊；開啟遊戲中的倉庫即可讀取庫房'
              : '沒有道具'}
          </div>
        )}
      </div>
    </Panel>
  );
}
