import { useMemo, useState } from 'react';
import type { CharacterDetail, SkillInfo } from '../../api/types';
import { StatNum } from '../../primitives';
import '../../components/items/items.css';

const ALL = 'all';
// Fixed groups listed after the sects, in this order.
const TAIL = ['bonus', 'meridian', 'general'];

type Group = { key: string; label: string; skills: SkillInfo[]; maxed: number };

export function SkillsTab({ detail, error }: { detail: CharacterDetail | null; error: string | null }) {
  const skills = detail?.skills;
  const [group, setGroup] = useState(ALL);
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const groups = useMemo(() => buildGroups(skills ?? []), [skills]);

  if (!detail) {
    return error
      ? <div role="status" style={{ color: 'var(--tt-bad)', fontSize: 13 }}>讀取失敗：{error}</div>
      : <div style={{ color: 'var(--tt-dim)' }}>讀取中…</div>;
  }
  if (skills == null) return <div className="ws-empty">等待角色定位後自動讀取</div>;
  if (skills.length === 0) return <div className="ws-empty">尚未習得任何技能</div>;

  // A group the next character does not have falls back to 全部.
  const current = groups.find(g => g.key === group) ?? groups[0];
  const q = query.trim();
  const rows = current.skills.filter(s => !q || s.name.includes(q) || s.description.includes(q));
  const selected = skills.find(s => s.magic_id === selectedId) ?? rows[0];

  return (
    <div className="sk-main">
      <div className="sk-side">
        <section className="sk-panel" aria-label="技能分類">
          <div className="sk-panel-t">分類</div>
          {groups.map(g => (
            <button
              key={g.key} type="button" className="sk-cat" aria-pressed={g.key === current.key}
              onClick={() => setGroup(g.key)}
            >
              <span className="sk-cat-n">{g.label}</span>
              <span className="sk-cat-c">{g.skills.length}</span>
              <span className="sk-cat-s">滿級 {g.maxed}</span>
              <span className="sk-cat-bar" aria-hidden="true">
                <span style={{ width: `${(100 * g.maxed) / g.skills.length}%` }} />
              </span>
            </button>
          ))}
        </section>
        <CapsPanel detail={detail} />
      </div>

      <section className="sk-panel" style={{ minWidth: 0 }}>
        <div className="inv-toolbar">
          <span className="sk-panel-t" style={{ padding: 0 }}>{current.label} · {rows.length}</span>
          <label className="inv-search">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
            </svg>
            <input type="search" value={query} onChange={e => setQuery(e.target.value)}
              placeholder="搜尋技能名稱或說明" aria-label="搜尋技能" />
          </label>
        </div>
        <div className="sk-head" aria-hidden="true"><span /><span>名稱</span><span>等級</span><span>真氣</span></div>
        {rows.length === 0
          ? <div className="inv-empty">沒有符合的技能</div>
          : rows.map(s => (
            <SkillRow key={s.magic_id} skill={s} selected={selected?.magic_id === s.magic_id}
              onSelect={() => setSelectedId(s.magic_id)} />
          ))}
      </section>

      {selected && <SkillDetail skill={selected} />}
    </div>
  );
}

/** 全部, then sects by size, then the fixed tail groups. */
function buildGroups(skills: SkillInfo[]): Group[] {
  const by = new Map<string, Group>();
  for (const s of skills) {
    let g = by.get(s.group);
    if (!g) by.set(s.group, g = { key: s.group, label: s.group_label, skills: [], maxed: 0 });
    g.skills.push(s);
    if (isMaxed(s)) g.maxed++;
  }
  const rank = (g: Group) => TAIL.indexOf(g.key);
  const sorted = [...by.values()].sort((a, b) =>
    rank(a) - rank(b) || (rank(a) < 0 ? b.skills.length - a.skills.length : 0));
  const all: Group = {
    key: ALL, label: '全部', skills, maxed: skills.filter(isMaxed).length,
  };
  return [all, ...sorted];
}

const isMaxed = (s: SkillInfo) => s.level >= s.max_level;

function SkillIcon({ skill, size }: { skill: SkillInfo; size: number }) {
  const [broken, setBroken] = useState<string | null>(null);
  const url = skill.icon_url;
  if (!url || broken === url) {
    return <span className="inv-icon-fallback" aria-hidden="true">{skill.name.slice(0, 1)}</span>;
  }
  return (
    <img src={url} alt="" loading="lazy" draggable={false} width={size} height={size}
      onError={() => setBroken(url)} />
  );
}

/** Level as segments; long tracks are scaled down to 20 so each stays visible. */
function LevelBar({ skill }: { skill: SkillInfo }) {
  const n = Math.min(skill.max_level, 20);
  const on = Math.round((skill.level * n) / skill.max_level);
  return (
    <span className="sk-segs" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => <span key={i} className={i < on ? 'is-on' : ''} />)}
    </span>
  );
}

function SkillRow({ skill, selected, onSelect }: { skill: SkillInfo; selected: boolean; onSelect: () => void }) {
  const maxed = isMaxed(skill);
  return (
    <button
      type="button" className="sk-row" aria-pressed={selected} onClick={onSelect}
      data-maxed={maxed || undefined} data-passive={skill.passive || undefined}
      aria-label={`${skill.name} 等級 ${skill.level} / ${skill.max_level}`}
    >
      <span className="sk-icon"><SkillIcon skill={skill} size={40} /></span>
      <span className="sk-name">
        <span className="sk-name-n">{skill.name}</span>
        <span className="sk-tags">
          <span className="inv-tag">{skill.passive ? '被動' : '主動'}</span>
          {maxed && <span className="inv-tag sk-tag-max">滿級</span>}
        </span>
      </span>
      <span className="sk-lv">
        <span className="sk-lv-t"><span>Lv {skill.level}</span><span>/ {skill.max_level}</span></span>
        <LevelBar skill={skill} />
      </span>
      <span className="sk-mp">{skill.mp_cost ? <StatNum value={skill.mp_cost} /> : '—'}</span>
    </button>
  );
}

function SkillDetail({ skill }: { skill: SkillInfo }) {
  return (
    <aside className="sk-panel sk-detail" aria-label="技能詳細">
      <div className="inv-d-head">
        <div className="inv-d-icon"><SkillIcon skill={skill} size={40} /></div>
        <div>
          <div className="inv-d-name">{skill.name}</div>
          <div className="inv-d-type">
            <span className="inv-tag">{skill.passive ? '被動' : '主動'}</span>
            <span className="inv-tag">{skill.group_label}</span>
          </div>
          <div className="inv-d-id">magic #{skill.magic_id}</div>
        </div>
      </div>
      <div className="inv-d-sec">
        <div className="sk-lv-t" style={{ fontSize: 14 }}>
          <span className="inv-d-label">等級</span>
          <span>{skill.level} <span style={{ color: 'var(--tt-dim)' }}>/ {skill.max_level}</span></span>
        </div>
        <span data-maxed={isMaxed(skill) || undefined} style={{ display: 'contents' }}>
          <LevelBar skill={skill} />
        </span>
      </div>
      {skill.mp_cost > 0 && (
        <div className="inv-d-sec">
          <dl className="inv-stats"><dt>消耗真氣</dt><dd><StatNum value={skill.mp_cost} /></dd></dl>
        </div>
      )}
      {skill.description && (
        <div className="inv-d-sec">
          <span className="inv-d-label">說明（目前等級）</span>
          <p className="sk-help">{skill.description}</p>
        </div>
      )}
    </aside>
  );
}

/** Flat bonuses the skills' help text adds; subtract them when deriving formulas. */
function CapsPanel({ detail }: { detail: CharacterDetail }) {
  const caps = detail.skill_caps ?? [];
  if (caps.length === 0) return null;
  return (
    <section className="sk-panel" style={{ padding: 12 }} aria-label="常駐上限加成">
      <div className="sk-panel-t" style={{ padding: '0 0 10px' }}>
        常駐上限加成 <span className="body-note">由技能說明推算</span>
      </div>
      <dl className="inv-stats">
        {caps.map(c => (
          <div key={c.label} style={{ display: 'contents' }}>
            <dt>{c.label}</dt>
            <dd className="body-signed">+<StatNum value={c.value} /></dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
