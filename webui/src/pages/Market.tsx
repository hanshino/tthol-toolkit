import { useEffect, useState } from 'react';
import { get, put } from '../api/client';
import type { CharacterRow, ItemMeta, MarketItemSummary, MarketListing, MarketTotals } from '../api/types';
import { GotoStall } from '../components/market/GotoStall';
import { GearInlays, GearStats } from '../components/items/GearStats';
import { ItemIcon } from '../components/items/ItemCells';
import { useItemMeta } from '../components/items/useItemMeta';
import { agoText, attrText, dateText, silverText } from '../components/market/format';
import { PriceCell } from '../components/market/PriceCell';
import { reportClientError } from '../diag/report';
import '../components/items/items.css';
import '../components/market/market.css';

const REFRESH_MS = 5000;

export function Market({ chars }: { chars: CharacterRow[] }) {
  const [totals, setTotals] = useState<MarketTotals | null>(null);
  const [items, setItems] = useState<MarketItemSummary[]>([]);
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [onlyActive, setOnlyActive] = useState(true);
  const [showNegotiate, setShowNegotiate] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);
  const [listings, setListings] = useState<MarketListing[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  // Search on pause, not on every keystroke.
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  // New stalls get recorded while this page is open; refresh quietly.
  useEffect(() => {
    const t = window.setInterval(() => setTick(n => n + 1), REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    let alive = true;
    const params = new URLSearchParams({
      q: query, include_ended: String(!onlyActive), include_negotiate: String(showNegotiate),
    });
    Promise.all([
      get<MarketTotals>('/api/market/totals'),
      get<MarketItemSummary[]>(`/api/market/items?${params}`),
    ])
      .then(([t, list]) => {
        if (!alive) return;
        setTotals(t);
        setItems(list);
        setError(null);
      })
      .catch(e => {
        if (!alive) return;
        setError(String(e));
        reportClientError(e, { component: 'Market.load', silent: true });
      });
    return () => { alive = false; };
  }, [query, onlyActive, showNegotiate, tick]);

  const current = items.find(i => i.item_id === selected) ?? items[0];
  const currentId = current?.item_id;

  useEffect(() => {
    if (currentId === undefined) { setListings([]); return; }
    let alive = true;
    get<MarketListing[]>(`/api/market/items/${currentId}/listings?include_ended=${!onlyActive}`)
      .then(rows => { if (alive) setListings(showNegotiate ? rows : rows.filter(r => r.price_kind !== 'negotiate')); })
      .catch(e => reportClientError(e, { component: 'Market.listings', silent: true }));
    return () => { alive = false; };
  }, [currentId, onlyActive, showNegotiate, tick]);

  const meta = useItemMeta([
    ...items.map(i => i.item_id),
    ...listings.flatMap(l => l.inlays.map(i => i.item_id)),
  ]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const toggle = (id: number) => setExpanded(prev => {
    const next = new Set(prev);
    if (!next.delete(id)) next.add(id);
    return next;
  });
  const [busy, setBusy] = useState(false);

  const mark = async (path: string, body: unknown) => {
    setBusy(true);
    try {
      await put(path, body);
      setTick(n => n + 1);
    } catch (e) {
      reportClientError(e, { component: 'Market.exclude' });
    } finally { setBusy(false); }
  };
  const excludeListing = (id: number, excluded: boolean) => mark(`/api/market/listings/${id}/excluded`, { excluded });
  const excludeSeller = (seller: string, excluded: boolean) => mark('/api/market/sellers/excluded', { seller, excluded });
  const curMeta = currentId !== undefined ? meta.get(currentId) : undefined;

  return (
    <div className="mk-page mk">
      <header className="mk-bar">
        <div style={{ display: 'grid', gap: 2 }}>
          <h1 style={{ margin: 0, fontFamily: 'var(--tt-font-serif)', fontSize: 20, fontWeight: 600, letterSpacing: 4 }}>市價</h1>
          <span className="mk-hint">從攤位記下的上架紀錄 · 同一筆上架重複看到只算一次</span>
        </div>
        <span className="mk-right">
          <a className="tt-btn" download href="/api/market/export.csv">匯出 CSV</a>
        </span>
      </header>

      <div className="mk-filters">
        <label htmlFor="mk-q">搜尋</label>
        <input id="mk-q" value={q} onChange={e => setQ(e.target.value)} placeholder="道具名稱" style={{ width: 220 }} />
        <label><input type="checkbox" checked={onlyActive} onChange={e => setOnlyActive(e.target.checked)} />只看架上</label>
        <label><input type="checkbox" checked={showNegotiate} onChange={e => setShowNegotiate(e.target.checked)} />顯示議價</label>
        {totals && (
          <span className="mk-right mk-mono mk-hint">
            {totals.stalls} 攤 · {totals.listings} 筆上架（議價 {totals.negotiate}）· 最後記錄 {agoText(totals.last_seen)}
          </span>
        )}
      </div>

      {error && <div className="ws-banner is-bad"><span className="ws-badge">讀取失敗</span>{error}</div>}

      {totals?.listings === 0
        ? <div className="ws-empty">還沒有任何紀錄。到市集地圖打開角色的「市集」分頁，逐一點開攤位就會開始累積。</div>
        : (
          <div className="mk-split">
            <div className="mk-box">
              <div className="mk-row" data-head style={{ gridTemplateColumns: '36px minmax(0, 1fr) auto' }}>
                <span /><span>道具</span><span>最低單價</span>
              </div>
              <div className="mk-scroll" style={{ maxHeight: 640 }}>
                {items.length === 0 && <div className="mk-empty">沒有符合的道具</div>}
                {items.map(i => (
                  <button
                    key={i.item_id} type="button" className="mk-item"
                    aria-pressed={i.item_id === currentId} onClick={() => setSelected(i.item_id)}
                  >
                    <span className="mk-icon mk-item-icon"><ItemIcon name={i.name} meta={meta.get(i.item_id)} size={32} /></span>
                    <span className="mk-ellipsis">{i.name}</span>
                    <span className="mk-num" style={{ color: i.min == null ? 'var(--tt-dim)' : 'var(--tt-gold)' }}>
                      {i.min == null ? '議價' : silverText(i.min)}
                    </span>
                    <span className="mk-item-meta">
                      {i.listings} 筆 · {i.sellers} 攤{i.negotiate ? ` · 議價 ${i.negotiate}` : ''}{i.flagged ? ` · 不採計 ${i.flagged}` : ''}
                    </span>
                    <span className="mk-item-meta mk-mono">{agoText(i.last_seen)}</span>
                  </button>
                ))}
              </div>
            </div>

            {current && (
              <div style={{ display: 'grid', gap: 12, minWidth: 0 }}>
                <div className="mk-box">
                  <div className="mk-head">
                    <span className="mk-icon" style={{ width: 44, height: 44 }}><ItemIcon name={current.name} meta={curMeta} size={40} /></span>
                    <div style={{ display: 'grid', gap: 2, minWidth: 0 }}>
                      <span style={{ fontFamily: 'var(--tt-font-serif)', fontSize: 17, letterSpacing: 2 }}>{current.name}</span>
                      <span className="mk-hint">{curMeta?.type_label || '—'}</span>
                    </div>
                    <div className="mk-stats">
                      <Stat k="最低" v={current.min} gold />
                      <Stat k="中位" v={current.median} />
                      <Stat k="最高" v={current.max} />
                      <div className="mk-stat">
                        <span className="mk-stat-k">筆數</span>
                        <span className="mk-stat-v" style={{ color: 'var(--tt-ok)' }}>{current.listings}</span>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mk-box">
                  <div className="mk-box-head">
                    <span className="mk-box-title">上架紀錄</span>
                    <span className="mk-hint">屬性不同的同名道具分開列</span>
                  </div>
                  <div className="mk-row mk-cols-list" data-head>
                    <span>攤主</span><span className="mk-end">單價</span><span className="mk-end">數量</span><span>屬性</span>
                    <span>首次看到</span><span>最後看到</span><span className="mk-end">狀態</span><span className="mk-end">採計</span>
                  </div>
                  <div className="mk-scroll" style={{ maxHeight: 460 }}>
                    {listings.length === 0 && <div className="mk-empty">沒有符合的上架紀錄</div>}
                    {listings.map(l => (
                      <ListingRow
                        key={l.id} l={l} busy={busy} meta={meta} chars={chars}
                        open={expanded.has(l.id)} onToggle={() => toggle(l.id)}
                        onExcludeListing={excludeListing} onExcludeSeller={excludeSeller}
                      />
                    ))}
                  </div>
                  <div className="mk-note">
                    價格判讀：99,999,999＝議價（飛鴿談），不計入最低／中位／最高；8 位數、開頭至少 4 個 9（例：99,999,200）＝官幣價，
                    尾數是百萬官幣數（1 百萬官幣＝100 萬銀兩），換算後計入。單價低於道具基準價 1/10 的標為「疑似誘餌價」，
                    和你按了「不採計」的一樣不計入行情。「已不在」＝重開那一攤時這筆不見了，可能賣掉或收回。
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
    </div>
  );
}

function Stat({ k, v, gold }: { k: string; v: number | null | undefined; gold?: boolean }) {
  return (
    <div className="mk-stat">
      <span className="mk-stat-k">{k}</span>
      <span className="mk-stat-v" style={{ color: gold ? 'var(--tt-gold)' : 'var(--tt-text)' }}>
        {v === null || v === undefined ? '—' : silverText(v)}
      </span>
    </div>
  );
}

/** Where to go: the stall's own tile when known, else where it was seen from. */
function placeText(l: MarketListing): string {
  const map = l.map || '—';
  if (l.x != null && l.y != null) return `${map} (${l.x}, ${l.y})`;
  if (l.viewer_x != null && l.viewer_y != null) return `${map} · 在 (${l.viewer_x}, ${l.viewer_y}) 附近`;
  return map;
}

function ListingRow({ l, busy, meta, chars, open, onToggle, onExcludeListing, onExcludeSeller }: {
  l: MarketListing; busy: boolean; meta: Map<number, ItemMeta>; chars: CharacterRow[]; open: boolean; onToggle: () => void;
  onExcludeListing: (id: number, excluded: boolean) => void;
  onExcludeSeller: (seller: string, excluded: boolean) => void;
}) {
  const hasGear = l.stats.length > 0 || l.inlays.length > 0 || l.plus > 0;
  return (
    <div className="mk-listing" data-open={open || undefined}>
      <div className="mk-row mk-cols-list" data-dim={l.ended_at || l.excluded ? true : undefined} style={{ padding: '8px 14px' }}>
        <button type="button" className="mk-who" aria-expanded={open} onClick={onToggle} title="展開詳情">
          <span className="mk-who-name">
            <svg className="mk-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
            {l.seller}
          </span>
          {l.sign && <span className="mk-ellipsis mk-attr">「{l.sign}」</span>}
          <span className="mk-ellipsis mk-attr mk-mono">{placeText(l)}</span>
        </button>
        <PriceCell p={l} />
        <span className="mk-num" style={{ color: 'var(--tt-dim)' }}>{l.count.toLocaleString()}</span>
        <span className="mk-ellipsis mk-attr">
          {l.plus > 0 && <span className="mk-plus">+{l.plus} </span>}
          {attrText(0, l.stats, l.inlays) || (l.plus > 0 ? '' : '—')}
        </span>
        <span className="mk-mono mk-attr">{dateText(l.first_seen)}</span>
        <span className="mk-mono mk-attr">{dateText(l.last_seen)}</span>
        <span className="mk-end mk-chips">
          {l.suspect && <span className="mk-chip" data-tone="warn" title="單價低於道具基準價的 1/10，通常買不到，是用來議價的">疑似誘餌價</span>}
          {l.ended_at ? <span className="mk-chip">已不在</span> : <span className="mk-chip" data-tone="ok">架上</span>}
        </span>
        <span className="mk-end mk-acts">
          {l.excluded === 'seller'
            ? <button type="button" className="mk-act" disabled={busy} onClick={() => onExcludeSeller(l.seller, false)} title={`恢復採計 ${l.seller} 的所有上架`}>恢復整攤</button>
            : (
              <>
                <button type="button" className="mk-act" disabled={busy} onClick={() => onExcludeListing(l.id, l.excluded !== 'listing')}>
                  {l.excluded === 'listing' ? '恢復' : '不採計'}
                </button>
                <button type="button" className="mk-act" disabled={busy} onClick={() => onExcludeSeller(l.seller, true)} title={`${l.seller} 的所有上架都不採計`}>整攤</button>
              </>
            )}
        </span>
      </div>
      {open && (
        <div className="mk-detail">
          <div className="inv-d-sec">
            <span className="inv-d-label">攤位</span>
            <dl className="inv-stats mk-detail-place">
              <dt>攤主</dt><dd>{l.seller}</dd>
              <dt>招牌</dt><dd>{l.sign ? `「${l.sign}」` : '—'}</dd>
              <dt>攤位位置</dt><dd>{l.x != null && l.y != null ? `${l.map} (${l.x}, ${l.y})` : '未知'}</dd>
              <dt>當時你站在</dt><dd>{l.viewer_x != null && l.viewer_y != null ? `(${l.viewer_x}, ${l.viewer_y})` : '—'}</dd>
              <dt>單價</dt><dd><PriceCell p={l} /></dd>
              <dt>數量</dt><dd>{l.count.toLocaleString()}</dd>
            </dl>
          </div>
          <GotoStall listing={l} chars={chars} />
          {hasGear
            ? (
              <>
                <GearStats gear={l} />
                <GearInlays inlays={l.inlays} meta={meta} />
              </>
            )
            : <div className="inv-d-sec mk-attr">這件道具沒有個別屬性，詳細說明和道具資料庫相同。</div>}
        </div>
      )}
    </div>
  );
}
