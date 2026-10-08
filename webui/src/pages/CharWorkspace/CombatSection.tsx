import type { AttackSkillCandidate, CombatRule } from '../../api/types';
import './guard.css';
import './daily.css';

// The character's one combat setting (character_settings "combat"), shared by
// every module that fights: 日常 (神武玄天塔) and 輔助 > 打怪.
// Like the battle puppet's 戰鬥 page: basic attack, one opener, three rotation skills.
export function CombatSection({ combat, skills, onChange }: {
  combat: CombatRule;
  skills: AttackSkillCandidate[];
  onChange: (c: CombatRule) => void;
}) {
  const rotation = [0, 1, 2].map(i => combat.rotation?.[i] ?? null);
  const setSlot = (i: number, v: number | null) => {
    const next = [...rotation];
    next[i] = v;
    onChange({ ...combat, rotation: next.filter((x): x is number => x != null) });
  };
  const options = (
    <>
      <option value="">（不用）</option>
      {skills.map(s => (
        <option key={s.magic_id} value={s.magic_id}>
          {s.name} Lv{s.level}・真氣 {s.mp}{s.area ? '・範圍' : ''}
        </option>
      ))}
    </>
  );
  return (
    <section className="gd-panel">
      <header className="gd-head">
        <h3>戰鬥</h3>
        <span className="gd-dim">照戰鬥木偶的設定：換目標先放首次攻擊，之後常用技能 1 → 2 → 3 輪流；真氣不夠的格子跳過</span>
      </header>
      <div className="dl-combat">
        <label className="dl-check">
          <input type="checkbox" checked={combat.basic ?? false} onChange={e => onChange({ ...combat, basic: e.target.checked })} />
          使用普通攻擊（和技能一起打，不是備用）
        </label>
        <label className="dl-field">
          <span>首次攻擊</span>
          <select value={combat.opener ?? ''} onChange={e => onChange({ ...combat, opener: e.target.value ? Number(e.target.value) : null })}>
            {options}
          </select>
        </label>
        {rotation.map((v, i) => (
          <label key={i} className="dl-field">
            <span>常用技能 {i + 1}</span>
            <select value={v ?? ''} onChange={e => setSlot(i, e.target.value ? Number(e.target.value) : null)}>
              {options}
            </select>
          </label>
        ))}
      </div>
      {skills.length === 0 && <div className="gd-dim">讀不到角色學會的攻擊技能。</div>}
      <div className="dl-combat dl-pick">
        <label className="dl-field">
          <span>選新目標</span>
          <select value={combat.target ?? 'nearest'}
            onChange={e => onChange({ ...combat, target: e.target.value as CombatRule['target'] })}>
            <option value="nearest">最近的先打</option>
            <option value="weakest">小怪先打（菁英最後）</option>
          </select>
        </label>
        <label className="dl-check dl-check-inline">
          <input type="checkbox" checked={combat.avoid_packs ?? false}
            onChange={e => onChange({ ...combat, avoid_packs: e.target.checked })} />
          避開怪群：先打周圍怪少的
        </label>
      </div>
      <div className="gd-fixed">
        <span>鎖定的怪沒死就一直打它，死了才換下一隻</span>
        <span>首次攻擊要看到命中才換常用技能</span>
        <span>2.2 秒沒打中就重新下攻擊指令</span>
        <span>下一招等上一招的冷卻＋硬直（至少 0.45 秒）</span>
        <span>守護補 buff 後停 1 秒</span>
      </div>
    </section>
  );
}
