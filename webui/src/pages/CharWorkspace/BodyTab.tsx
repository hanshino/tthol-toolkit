import type { CharacterDetail } from '../../api/types';
import { Panel, StatNum } from '../../primitives';

export function BodyTab({ detail, error }: { detail: CharacterDetail | null; error: string | null }) {
  if (!detail) {
    return error
      ? <div role="status" style={{ color: 'var(--tt-bad)', fontSize: 13 }}>讀取失敗：{error}</div>
      : <div style={{ color: 'var(--tt-dim)' }}>讀取中…</div>;
  }
  const s = detail.stats;
  const six = [
    ['外功', s.waigong], ['內力', s.neili], ['根骨', s.genggu],
    ['身法', s.shenfa], ['技巧', s.jiqiao], ['玄學', s.xuanxue],
  ] as const;
  const seven = [
    ['物攻', s.wugong], ['基礎', s.wugong_base], ['內勁', s.neijing],
    ['防禦', s.fangyu], ['護勁', s.huji], ['命中', s.mingzhong], ['閃躲', s.shanduo],
  ] as const;
  // Buffs live in the workspace header now, so this tab shows stats only.
  return (
    <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1fr' }}>
      <Panel title="六屬"><Grid pairs={six} /></Panel>
      <Panel title="七戰"><Grid pairs={seven} /></Panel>
    </div>
  );
}

function Grid({ pairs }: { pairs: readonly (readonly [string, number])[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
      {pairs.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: 'var(--tt-dim)', letterSpacing: 2 }}>{k}</span>
          <StatNum value={v} />
        </div>
      ))}
    </div>
  );
}
