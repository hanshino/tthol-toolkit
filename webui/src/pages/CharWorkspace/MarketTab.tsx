import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { get, put } from '../../api/client';
import type { MarketMode, MarketStallInView, MarketStatus } from '../../api/types';
import { ItemIcon } from '../../components/items/ItemCells';
import { useItemMeta } from '../../components/items/useItemMeta';
import { agoText, attrText, clockText } from '../../components/market/format';
import { PriceCell } from '../../components/market/PriceCell';
import { reportClientError } from '../../diag/report';
import '../../components/items/items.css';
import '../../components/market/market.css';

const POLL_MS = 1000;

const MODES: { k: MarketMode; n: string }[] = [
  { k: 'auto', n: '自動 · 市集地圖' },
  { k: 'on', n: '手動開始' },
  { k: 'off', n: '停止' },
];

function stateText(s: MarketStatus): string {
  if (s.active) return `${s.map_name ?? '—'} · 記錄中`;
  if (s.reason === 'not_market') return `${s.map_name ?? '—'} · 不在市集，暫停`;
  if (s.reason === 'off') return '已停止';
  if (s.reason === 'no_character') return '角色尚未定位';
  return '準備中';
}

export function MarketTab({ pid, onOpenPrices }: { pid: number; onOpenPrices: () => void }) {
  const [status, setStatus] = useState<MarketStatus | null>(null);
  const [busy, setBusy] = useState(false);
  // One request at a time, and none applied after the tab is torn down: a slow
  // reply must not land on top of a newer one.
  const inflight = useRef(false);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const s = await get<MarketStatus>(`/api/characters/${pid}/market/status`);
      if (alive.current) setStatus(s);
    } catch (e) {
      reportClientError(e, { component: 'MarketTab.refresh', silent: true });
    } finally {
      inflight.current = false;
    }
  }, [pid]);

  useEffect(() => {
    alive.current = true;
    refresh();
    const t = window.setInterval(refresh, POLL_MS);
    return () => { alive.current = false; window.clearInterval(t); };
  }, [refresh]);

  const setMode = async (mode: MarketMode) => {
    setBusy(true);
    try {
      await put(`/api/characters/${pid}/market/mode`, { mode });
      await refresh();
    } catch (e) {
      reportClientError(e, { component: 'MarketTab.setMode' });
    } finally { setBusy(false); }
  };

  const cur = status?.current ?? null;
  const meta = useItemMeta([...(cur?.rows ?? []), ...(cur?.gone ?? [])].map(r => r.item_id));
  const stalls = useMemo(() => sortStalls(status?.stalls ?? [], cur?.open ? cur.seller : null), [status, cur]);

  if (status === null) return <div className="ws-empty">讀取市集狀態中…</div>;
  const seen = stalls.filter(s => s.last_recorded).length;

  return (
    <div className="mk">
      <div className="mk-bar">
        <div className="mk-modes" role="radiogroup" aria-label="記錄方式">
          {MODES.map(m => (
            <button
              key={m.k} type="button" role="radio" className="mk-mode" disabled={busy}
              aria-checked={status.mode === m.k} onClick={() => setMode(m.k)}
            >
              {m.n}
            </button>
          ))}
        </div>
        <span className="mk-state" data-on={status.active || undefined}>{stateText(status)}</span>
        <span className="mk-hint">逐一點開攤位就會自動記下；重開同一攤不會重複計算</span>
        <span className="mk-right mk-mono mk-hint">
          本次 {status.session.stalls} 攤 · 新上架 {status.session.new} 筆 · 讀取 {status.session.reads} 次
        </span>
      </div>

      <div className="mk-split">
        <div className="mk-box">
          <div className="mk-box-head">
            <span className="mk-box-title">視野內攤位</span>
            <span className="mk-mono mk-hint">{stalls.length} 攤 · 已記錄 {seen}</span>
          </div>
          <div className="mk-scroll">
            {stalls.length === 0
              ? <div className="mk-empty">{status.active ? '附近沒有擺攤的玩家' : '開始記錄後會列出附近的攤位'}</div>
              : stalls.map(s => <StallLine key={s.seller} s={s} open={cur?.open === true && cur.seller === s.seller} />)}
          </div>
        </div>

        <div style={{ display: 'grid', gap: 12, minWidth: 0 }}>
          <div className="mk-box" data-live={cur?.open || undefined}>
            {cur === null
              ? <div className="mk-empty">在遊戲裡點開一個攤位，商品和價格就會出現在這裡。<br />NPC 商店不會記錄。</div>
              : (
                <>
                  <div className="mk-box-head" style={{ justifyContent: 'flex-start' }}>
                    <span className="mk-chip" data-tone={cur.open ? 'gold' : undefined}>{cur.open ? '開啟中' : '已關閉'}</span>
                    <span className="mk-box-title" style={{ letterSpacing: 2, fontSize: 16 }}>{cur.seller}</span>
                    {cur.sign && <span className="mk-hint">「{cur.sign}」</span>}
                    <span className="mk-right mk-mono mk-hint">{clockText(cur.recorded_at)} · {cur.settle_s} 秒讀完</span>
                  </div>
                  <div className="mk-sum">
                    {cur.new > 0 && <span style={{ color: 'var(--tt-ok)' }}>新上架 {cur.new} 筆</span>}
                    {cur.unchanged > 0 && <span style={{ color: 'var(--tt-dim)' }}>未變 {cur.unchanged} 筆</span>}
                    {cur.changed > 0 && <span style={{ color: 'var(--tt-warn)' }}>數量變化 {cur.changed} 筆</span>}
                    {cur.gone.length > 0 && <span style={{ color: 'var(--tt-mute)' }}>已不在 {cur.gone.length} 筆</span>}
                    {cur.new === 0 && <span className="mk-right" style={{ color: 'var(--tt-mute)' }}>只更新「最後看到」，不會多算一筆</span>}
                  </div>
                  <div className="mk-row mk-cols-stall" data-head>
                    <span /><span>道具</span><span>屬性</span><span className="mk-end">單價</span><span className="mk-end">數量</span><span className="mk-end">狀態</span>
                  </div>
                  <div className="mk-scroll" style={{ maxHeight: 420 }}>
                    {cur.rows.map((r, i) => {
                      const m = meta.get(r.item_id);
                      const name = m?.name ?? `#${r.item_id}`;
                      return (
                        <div key={i} className="mk-row mk-cols-stall">
                          <span className="mk-icon"><ItemIcon name={name} meta={m} size={32} /></span>
                          <span className="mk-ellipsis">{name}{r.plus > 0 && <span style={{ color: 'var(--tt-gold)' }}> +{r.plus}</span>}</span>
                          <span className="mk-ellipsis mk-attr">{attrText(0, r.stats, r.inlays) || '—'}</span>
                          <PriceCell p={r} />
                          <span className="mk-num" style={{ color: 'var(--tt-dim)' }}>{r.count.toLocaleString()}</span>
                          <span className="mk-end mk-chips">
                            {r.suspect && <span className="mk-chip" data-tone="warn" title="單價低於道具基準價的 1/10，通常買不到，是用來議價的">疑似誘餌價</span>}
                            <RowStatus status={r.status} old={r.old_count} count={r.count} />
                          </span>
                        </div>
                      );
                    })}
                    {cur.gone.map((g, i) => {
                      const m = meta.get(g.item_id);
                      const name = m?.name ?? `#${g.item_id}`;
                      return (
                        <div key={`g${i}`} className="mk-row mk-cols-stall" data-dim>
                          <span className="mk-icon"><ItemIcon name={name} meta={m} size={32} /></span>
                          <span className="mk-ellipsis">{name}</span>
                          <span className="mk-attr">這次沒看到：賣掉或收回</span>
                          <PriceCell p={g} />
                          <span className="mk-num">—</span>
                          <span className="mk-end"><span className="mk-chip">已不在</span></span>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
          </div>

          <div className="mk-box">
            <div className="mk-box-head">
              <span className="mk-box-title">本次紀錄</span>
              <button type="button" className="is-ghost" onClick={onOpenPrices}>到市價查看全部 ›</button>
            </div>
            <div className="mk-scroll mk-log" style={{ maxHeight: 180 }}>
              {status.log.length === 0
                ? <div className="mk-empty">還沒有紀錄</div>
                : status.log.map((l, i) => (
                  <div key={i} className="mk-log-row" data-kind={l.kind}>
                    <span className="mk-mono" style={{ color: 'var(--tt-mute)' }}>{clockText(l.t)}</span>
                    <span className="mk-ellipsis" style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 1 }}>{l.seller ?? '—'}</span>
                    <span className="mk-log-text">{l.refresh ? '（更新）' : ''}{l.text}</span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function sortStalls(stalls: MarketStallInView[], open: string | null): MarketStallInView[] {
  const rank = (s: MarketStallInView) => (s.seller === open ? 0 : s.last_recorded ? 2 : 1);
  return [...stalls].sort((a, b) => rank(a) - rank(b) || a.seller.localeCompare(b.seller));
}

function StallLine({ s, open }: { s: MarketStallInView; open: boolean }) {
  const chip = open
    ? <span className="mk-chip" data-tone="gold">開啟中</span>
    : s.last_recorded
      ? <span className="mk-chip" data-tone="ok">已記錄 · {agoText(s.last_recorded)}</span>
      : <span className="mk-chip">未看過</span>;
  return (
    <div className="mk-stall" data-open={open || undefined}>
      <span className="mk-stall-name">{s.seller}</span>
      {chip}
      <span className="mk-stall-sign">{s.sign ? `「${s.sign}」` : '（無招牌）'}</span>
    </div>
  );
}

function RowStatus({ status, old, count }: { status: string; old?: number | null; count: number }) {
  if (status === 'new') return <span className="mk-chip" data-tone="ok">新上架</span>;
  if (status === 'changed') return <span className="mk-chip" data-tone="warn">{old} → {count}</span>;
  return <span className="mk-chip">未變</span>;
}
