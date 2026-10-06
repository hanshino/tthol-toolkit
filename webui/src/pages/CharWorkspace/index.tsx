import { useRef } from 'react';
import type { CharacterRow } from '../../api/types';
import { can, isStopped, isUnlocated, type CharTab, type GlobalView } from '../../nav';
import { AutoClickTab } from './AutoClickTab';
import { GuardPanel } from './GuardPanel';
import { BodyTab } from './BodyTab';
import { CharHeader } from './CharHeader';
import { ChatTab } from './ChatTab';
import { DailyTab } from './DailyTab';
import { DamageTab } from './DamageTab';
import { ItemsTab } from './ItemsTab';
import { MapAnalysis } from './MapAnalysis';
import { MarketTab } from './MarketTab';
import { SkillsTab } from './SkillsTab';
import { useCharacterDetail } from './useCharacterDetail';
import './workspace.css';

const TABS: { k: CharTab; n: string; s: string }[] = [
  { k: 'items', n: '行囊', s: '道具' },
  { k: 'body', n: '根脈', s: '屬性 · 裝備' },
  { k: 'skills', n: '武學', s: '技能 · 經脈' },
  { k: 'maps', n: '行止', s: '地圖' },
  { k: 'market', n: '市集', s: '攤位調查' },
  { k: 'damage', n: '戰錄', s: '傷害 · DPS' },
  { k: 'assist', n: '輔助', s: '守護 · 英雄培養' },
];
// Only when the client's hook allows the feature (see `can`): 日常 walks,
// fights and talks through hook commands, 傳音 reads its chat packets.
const HOOK_TABS: { k: CharTab; n: string; s: string; feature: string }[] = [
  { k: 'daily', n: '日常', s: '玄天塔', feature: 'daily.' },
  { k: 'chat', n: '傳音', s: '聊天', feature: 'chat' },
];

export function CharWorkspace({ char, goneSince, tab, onTab, onNav }: {
  char: CharacterRow; goneSince: number | null; tab: CharTab;
  onTab: (t: CharTab) => void; onNav: (k: GlobalView) => void;
}) {
  const gone = goneSince !== null;
  const unlocated = isUnlocated(char);
  const stale = gone || isStopped(char) || unlocated;
  // A gone pid has no session; polling it would only fail every 3 s.
  const { detail, error } = useCharacterDetail(char.pid, !unlocated && !gone);
  // Tabs mount on first visit and then stay mounted (hidden), so search text,
  // filters and selection survive a tab switch. Keyed by pid in App, so a
  // different character starts fresh.
  const visited = useRef(new Set<CharTab>());
  visited.current.add(tab);
  // Once opened it stays even if the hook drops, so it does not vanish under the user.
  const tabs = [...TABS, ...HOOK_TABS.filter(t => can(char, t.feature) || visited.current.has(t.k))];

  return (
    <div className="ws">
      <div className="ws-sticky">
        <CharHeader char={char} goneSince={goneSince} onBackToOverview={() => onNav('overview')} />
        <nav className="ws-tabs" role="tablist" aria-label="角色分頁">
          {tabs.map(t => (
            <button
              key={t.k} type="button" role="tab" className="ws-tab"
              aria-selected={tab === t.k} onClick={() => onTab(t.k)}
            >
              <span className="ws-tab-n">{t.n}</span>
              <span className="ws-tab-s">{t.s}</span>
            </button>
          ))}
        </nav>
      </div>
      <div className="ws-body" data-stale={stale || undefined}>
        {/* 重偵/relocate briefly turns a located character back into a
            placeholder row; keep its tabs (and their search/filters) mounted
            on the last detail instead of wiping them. */}
        {unlocated && detail === null
          ? <div className="ws-empty">角色定位後，這裡會自動讀取行囊、屬性、武學與地圖</div>
          : tabs.filter(t => visited.current.has(t.k)).map(t => (
            <div key={t.k} role="tabpanel" hidden={tab !== t.k}>
              {t.k === 'items' && (
                <ItemsTab pid={char.pid} detail={detail} error={error} onOpenSnapshots={() => onNav('snapshots')} />
              )}
              {t.k === 'body' && <BodyTab detail={detail} error={error} />}
              {t.k === 'skills' && <SkillsTab detail={detail} error={error} />}
              {t.k === 'maps' && <MapAnalysis char={char} active={tab === 'maps'} />}
              {t.k === 'market' && <MarketTab pid={char.pid} onOpenPrices={() => onNav('market')} />}
              {t.k === 'damage' && <DamageTab pid={char.pid} active={tab === 'damage'} />}
              {t.k === 'assist' && (
                <div style={{ display: 'grid', gap: 14 }}>
                  <GuardPanel pid={char.pid} active={tab === 'assist'} />
                  <AutoClickTab pid={char.pid} />
                </div>
              )}
              {t.k === 'daily' && <DailyTab pid={char.pid} active={tab === 'daily'} />}
              {t.k === 'chat' && <ChatTab pid={char.pid} active={tab === 'chat'} />}
            </div>
          ))}
      </div>
    </div>
  );
}
