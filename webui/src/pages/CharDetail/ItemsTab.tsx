import { useEffect, useState } from 'react';
import { get, post } from '../../api/client';
import { describeError, reportClientError } from '../../diag/report';
import type { CharacterDetail, SaveSnapshotResult } from '../../api/types';
import { LinkDot, type LinkStatus } from '../../primitives';
import { buildEntries, CATEGORIES, groupByCategory, SOURCE_LABEL, type Category, type Entry, type Tab } from '../../components/items/entries';
import { ContainerHoldings, ItemDetail } from '../../components/items/ItemDetail';
import { Row, Slot } from '../../components/items/ItemCells';
import { useItemMeta } from '../../components/items/useItemMeta';
import '../../components/items/items.css';

// The worker re-reads the bag every poll (~3 s); older than this means it stalled.
const STALE_MS = 10_000;
const PREFS_KEY = 'tthol.items.prefs';

type View = 'grid' | 'list';
type SnapshotSource = 'inventory' | 'warehouse';

function clock(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });
}

function loadPrefs(): { view: View; merge: boolean } {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}');
    return { view: p.view === 'list' ? 'list' : 'grid', merge: p.merge !== false };
  } catch {
    return { view: 'grid', merge: true };
  }
}

function SummaryCell({ label, status, value, unit, sub, gold }: {
  label: string; status?: LinkStatus; value: string; unit?: string; sub: string; gold?: boolean;
}) {
  return (
    <div className="inv-sum-cell">
      <span className="inv-sum-label">{status && <LinkDot status={status} size={7} />}{label}</span>
      <span className={gold ? 'inv-sum-value is-gold' : 'inv-sum-value'}>
        {value}{unit && <small>{unit}</small>}
      </span>
      <span className="inv-sum-sub">{sub}</span>
    </div>
  );
}

export function ItemsTab({ pid }: { pid: number }) {
  const [detail, setDetail] = useState<CharacterDetail | null>(null);
  const [tab, setTab] = useState<Tab>('inventory');
  const [category, setCategory] = useState<Category | 'all'>('all');
  const [query, setQuery] = useState('');
  const [prefs, setPrefs] = useState(loadPrefs);
  const [selected, setSelected] = useState<string | null>(null);
  const [saving, setSaving] = useState<SnapshotSource | null>(null);
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
    const id = setInterval(fetchOnce, 3000); // containers are re-read by the worker every poll
    return () => { cancelled = true; clearInterval(id); };
  }, [pid]);

  const updatePrefs = (next: Partial<typeof prefs>) => {
    const merged = { ...prefs, ...next };
    setPrefs(merged);
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(merged)); } catch { /* storage blocked */ }
  };

  const saveSnapshot = async (source: SnapshotSource) => {
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
  const pet = detail?.pet_inventory ?? [];
  const warehouse = detail?.warehouse ?? [];
  const slots = [...inventory, ...pet, ...warehouse];
  const meta = useItemMeta(slots.map(s => s.item_id));

  const entries = buildEntries(slots, meta, { tab, merge: prefs.merge, query });
  const shown = category === 'all' ? entries : entries.filter(e => e.category === category);
  const current: Entry | undefined = shown.find(e => e.key === selected) ?? shown[0];

  const invTs = detail?.inventory_updated_at ?? null;
  const invFresh = invTs !== null && Date.now() - invTs * 1000 < STALE_MS;
  const invStatus: LinkStatus = invFresh ? 'ok' : 'weak';
  const invSub = invTs === null
    ? '等待角色定位後自動讀取'
    : invFresh ? `自動更新中 · ${clock(invTs)}` : `暫停更新 · 最後 ${clock(invTs)}`;

  const whTs = detail?.warehouse_updated_at ?? null;
  const whOpen = detail?.warehouse_open ?? false;
  const whSub = whOpen
    ? `倉庫開啟中 · 自動更新`
    : whTs === null ? '在遊戲中開啟倉庫即可讀取' : `倉庫已關閉 · 顯示 ${clock(whTs)} 的內容`;

  const counts: Record<Tab, number> = {
    all: slots.length, inventory: inventory.length, pet: pet.length, warehouse: warehouse.length,
  };
  const catCounts = new Map<Category, number>();
  for (const e of entries) catCounts.set(e.category, (catCounts.get(e.category) ?? 0) + 1);

  const showSources = tab === 'all';
  // Raw slot order (as in game) only when nothing regroups or merges the slots.
  const flat = prefs.view === 'grid' && !prefs.merge && category === 'all' && !query.trim();

  const renderEntries = (list: Entry[]) => prefs.view === 'grid'
    ? (
      <div className="inv-grid">
        {list.map(e => (
          <Slot key={e.key} entry={e} selected={e.key === current?.key} showSources={showSources}
            onSelect={() => setSelected(e.key)} />
        ))}
      </div>
    )
    : (
      <div className="inv-list">
        {list.map(e => (
          <Row key={e.key} entry={e} selected={e.key === current?.key} showSources={showSources}
            onSelect={() => setSelected(e.key)} />
        ))}
      </div>
    );

  const emptyText = query.trim()
    ? `沒有符合「${query.trim()}」的道具`
    : tab === 'warehouse' && whTs === null
      ? '尚未讀取庫房 — 在遊戲中開啟倉庫即可自動讀取'
      : invTs === null ? '角色定位後會自動讀取行囊' : '這裡沒有道具';

  const totalQty = shown.reduce((a, e) => a + e.qty, 0);

  return (
    <div className="inv">
      <section className="inv-summary" aria-label="總覽">
        <SummaryCell label="銀兩" gold
          value={detail?.money != null ? detail.money.toLocaleString() : '—'} unit={detail?.money != null ? '兩' : undefined}
          sub={detail?.money != null && detail.money >= 10000 ? `約 ${Math.floor(detail.money / 10000).toLocaleString()} 萬` : ' '} />
        <SummaryCell label="行囊" status={invStatus} value={String(inventory.length)} unit="格" sub={invSub} />
        <SummaryCell label="寵物背包" status={invStatus} value={String(pet.length)} unit="格" sub={invSub} />
        <SummaryCell label="庫房" status={whOpen ? 'ok' : 'weak'} value={String(warehouse.length)} unit="格" sub={whSub} />
      </section>

      <div className="inv-main">
        <section className="inv-box" aria-label="道具">
          <div className="inv-tabs" role="tablist">
            {(['all', 'inventory', 'pet', 'warehouse'] as const).map(t => (
              <button key={t} type="button" role="tab" className="inv-tab" aria-selected={tab === t}
                onClick={() => { setTab(t); setCategory('all'); setSelected(null); }}>
                {t === 'all' ? '全部' : SOURCE_LABEL[t]}
                <span className="inv-n">{counts[t]}</span>
                {t === 'warehouse' && !whOpen && whTs !== null && <span className="inv-tab-note">已關閉</span>}
              </button>
            ))}
          </div>

          <div className="inv-toolbar">
            <label className="inv-search">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
              </svg>
              <input type="search" value={query} onChange={e => setQuery(e.target.value)}
                placeholder="搜尋名稱或說明（例：金創、+30）" aria-label="搜尋道具" />
            </label>
            <button type="button" className={prefs.merge ? 'is-active' : ''} aria-pressed={prefs.merge}
              title="同一種道具佔好幾格時，合成一格顯示總數"
              onClick={() => { updatePrefs({ merge: !prefs.merge }); setSelected(null); }}>
              合併同名
            </button>
            <div className="inv-seg" role="group" aria-label="檢視方式">
              <button type="button" className={prefs.view === 'grid' ? 'is-active' : ''} aria-pressed={prefs.view === 'grid'}
                onClick={() => updatePrefs({ view: 'grid' })}>格子</button>
              <button type="button" className={prefs.view === 'list' ? 'is-active' : ''} aria-pressed={prefs.view === 'list'}
                onClick={() => updatePrefs({ view: 'list' })}>清單</button>
            </div>
          </div>

          {entries.length > 0 && (
            <div className="inv-chips">
              <button type="button" className="inv-chip" aria-pressed={category === 'all'} onClick={() => setCategory('all')}>
                全部<span className="inv-n">{entries.length}</span>
              </button>
              {CATEGORIES.filter(c => catCounts.has(c.key)).map(c => (
                <button key={c.key} type="button" className="inv-chip" aria-pressed={category === c.key}
                  onClick={() => setCategory(c.key)}>
                  <span className="inv-cdot" style={{ background: c.color }} />
                  {c.label}<span className="inv-n">{catCounts.get(c.key)}</span>
                </button>
              ))}
            </div>
          )}

          {toast && <div className="inv-toast">{toast}</div>}

          <div className="inv-body">
            {shown.length === 0
              ? <div className="inv-empty">{emptyText}</div>
              : flat
                ? renderEntries(shown)
                : groupByCategory(shown).map(g => (
                  <div key={g.key} className="inv-group">
                    <div className="inv-group-h">{g.label}<span className="inv-n">{g.entries.length}</span></div>
                    {renderEntries(g.entries)}
                  </div>
                ))}
          </div>

          <div className="inv-foot">
            <span>
              {prefs.merge ? `${shown.length} 種道具` : `${shown.length} 格`} · 共 {totalQty.toLocaleString()} 個
            </span>
            <span style={{ display: 'flex', gap: 6 }}>
              <button type="button" className="is-ghost" onClick={() => saveSnapshot('inventory')}
                disabled={inventory.length === 0 || saving !== null} title="將目前行囊內容存入留影">
                {saving === 'inventory' ? '保存中…' : '↧ 留影身'}
              </button>
              <button type="button" className="is-ghost" onClick={() => saveSnapshot('warehouse')}
                disabled={warehouse.length === 0 || saving !== null} title="將目前庫房內容存入留影">
                {saving === 'warehouse' ? '保存中…' : '↧ 留影庫'}
              </button>
            </span>
          </div>
        </section>

        <ItemDetail entry={current}>
          {current && <ContainerHoldings slots={slots} itemId={current.itemId} />}
        </ItemDetail>
      </div>
    </div>
  );
}
