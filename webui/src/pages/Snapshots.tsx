import { type ChangeEvent, useCallback, useEffect, useRef, useState } from 'react';
import { get, upload } from '../api/client';
import { describeError, reportClientError } from '../diag/report';
import type { BackupImportResult, SnapshotRow } from '../api/types';
import { Panel } from '../primitives';

type Status = { kind: 'ok' | 'err'; text: string } | null;

export function Snapshots() {
  const [rows, setRows] = useState<SnapshotRow[]>([]);
  const [selected, setSelected] = useState<SnapshotRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadRows = useCallback(
    () => get<SnapshotRow[]>('/api/snapshots')
      .then(setRows)
      .catch(e => {
        setStatus({ kind: 'err', text: `讀取留影失敗：${describeError(e)}` });
        reportClientError(e, { component: 'Snapshots.loadRows' });
      }),
    [],
  );

  useEffect(() => { loadRows(); }, [loadRows]);

  const onImportPick = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';  // allow re-importing the same file
    if (!file) return;
    setBusy(true);
    setStatus(null);
    try {
      const r = await upload<BackupImportResult>('/api/backup/import', file);
      setStatus({
        kind: 'ok',
        text: `新增 ${r.snapshots_added} 筆 / 略過 ${r.snapshots_skipped} 筆 / 帳號衝突 ${r.account_conflicts} 個`,
      });
      await loadRows();
    } catch (err) {
      setStatus({ kind: 'err', text: importErrorText(err) });
      reportClientError(err, { component: 'Snapshots.import' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: 'grid', gap: 16, padding: 16 }}>
      <Panel title="系統備份">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <a className="tt-btn" href="/api/backup/export" download>匯出備份</a>
          <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
            {busy ? '匯入中…' : '匯入備份'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            onChange={onImportPick}
            style={{ display: 'none' }}
          />
          <span style={{ color: 'var(--tt-mute)', fontSize: 11, letterSpacing: 1 }}>
            匯出整個留影資料庫為 JSON；匯入會合併既有備份，不覆蓋現有資料
          </span>
        </div>
        {status && (
          <div style={{
            marginTop: 10, fontSize: 12,
            color: status.kind === 'ok' ? 'var(--tt-ok)' : 'var(--tt-bad)',
          }}>
            {status.text}
          </div>
        )}
      </Panel>

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 16 }}>
        <Panel title="留影列表">
          <div style={{ display: 'grid', gap: 4 }}>
            {rows.map(r => (
              <button
                key={r.snapshot_id}
                onClick={() => setSelected(r)}
                style={{
                  textAlign: 'left', padding: 8, background: selected?.snapshot_id === r.snapshot_id ? 'var(--tt-raised)' : 'transparent',
                  border: '1px solid var(--tt-line-soft)', color: 'var(--tt-text)', cursor: 'pointer',
                }}
              >
                <div style={{ fontFamily: 'var(--tt-font-serif)', letterSpacing: 2 }}>{r.character_name}</div>
                <div style={{ fontSize: 11, color: 'var(--tt-mute)', fontFamily: 'var(--tt-font-mono)' }}>
                  {r.saved_at} · {r.source} · {r.item_count} 件
                </div>
              </button>
            ))}
          </div>
        </Panel>
        <Panel title="留影內容">
          {selected ? (
            <div>
              <div style={{ marginBottom: 12 }}>
                <strong style={{ fontFamily: 'var(--tt-font-serif)' }}>{selected.character_name}</strong>
                <span style={{ color: 'var(--tt-mute)', marginLeft: 8 }}>{selected.saved_at}</span>
              </div>
              <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>
                {selected.item_count} 件道具（道具明細 v1.1 接入；diff 已延後）
              </div>
            </div>
          ) : (
            <div style={{ color: 'var(--tt-mute)', fontSize: 12 }}>選擇一筆留影查看內容</div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function importErrorText(err: unknown): string {
  const msg = String(err);
  if (msg.includes(': 400')) return '匯入失敗：備份檔格式無效或版本不支援';
  if (msg.includes(': 503')) return '匯入失敗：系統備份服務尚未就緒';
  return '匯入失敗，請稍後再試';
}
