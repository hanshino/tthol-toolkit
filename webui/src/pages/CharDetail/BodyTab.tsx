import { useEffect, useState } from 'react';
import { get } from '../../api/client';
import { Panel, StatNum } from '../../primitives';

interface Detail {
  pid: number; name: string;
  stats: {
    waigong: number; neili: number; genggu: number; shenfa: number; jiqiao: number; xuanxue: number;
    wugong: number; wugong_base: number; neijing: number; fangyu: number; huji: number; mingzhong: number; shanduo: number;
  };
}

export function BodyTab({ pid }: { pid: number }) {
  const [d, setD] = useState<Detail | null>(null);
  useEffect(() => { get<Detail>(`/api/characters/${pid}`).then(setD).catch(() => {}); }, [pid]);
  if (!d) return <div style={{ color: 'var(--tt-mute)' }}>讀取中…</div>;
  const six = [
    ['外功', d.stats.waigong], ['內力', d.stats.neili], ['根骨', d.stats.genggu],
    ['身法', d.stats.shenfa], ['技巧', d.stats.jiqiao], ['玄學', d.stats.xuanxue],
  ] as const;
  const seven = [
    ['物攻', d.stats.wugong], ['基礎', d.stats.wugong_base], ['內勁', d.stats.neijing],
    ['防禦', d.stats.fangyu], ['護勁', d.stats.huji], ['命中', d.stats.mingzhong], ['閃躲', d.stats.shanduo],
  ] as const;
  return (
    <div style={{ display: 'grid', gap: 16, gridTemplateColumns: '1fr 1fr' }}>
      <Panel title="六屬">
        <Grid pairs={six} />
      </Panel>
      <Panel title="七戰">
        <Grid pairs={seven} />
      </Panel>
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
