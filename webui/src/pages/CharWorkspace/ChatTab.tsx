import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { get } from '../../api/client';
import type { ChatLog, ChatMessage } from '../../api/types';
import './chat.css';

// Chat the hook pipe delivered (services/hook_hub.py). The backend keeps the
// last 500 while this tab is hidden; the tab only polls while visible and
// picks up what it missed by seq.
const POLL_MS = 1000;
const KEEP = 500;

const CHANNELS: { k: string; n: string }[] = [
  { k: 'normal', n: '一般' },
  { k: 'whisper', n: '密語' },
  { k: 'party', n: '組隊' },
  { k: 'family', n: '家族' },
  { k: 'area', n: '區域' },
  { k: 'shout', n: '世界' },
];
const CHANNEL_NAME: Record<string, string> = Object.fromEntries(CHANNELS.map(c => [c.k, c.n]));

const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-TW', { hour12: false });

type Feed = { messages: ChatMessage[]; lastSeq: number; connected: boolean; proto: number | null; failed: boolean };

function useChat(pid: number, enabled: boolean): Feed {
  const [feed, setFeed] = useState<Feed>({ messages: [], lastSeq: 0, connected: false, proto: null, failed: false });
  const seq = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer = 0;
    const poll = () => {
      get<ChatLog>(`/api/characters/${pid}/chat?after=${seq.current}`)
        .then((d) => {
          if (cancelled) return;
          // The backend restarted: its seqs start over, so does the list.
          const reset = d.last_seq < seq.current;
          seq.current = d.last_seq;
          setFeed((f) => ({
            messages: (reset ? d.messages : [...f.messages, ...d.messages]).slice(-KEEP),
            lastSeq: d.last_seq, connected: d.connected, proto: d.proto ?? null, failed: false,
          }));
        })
        .catch(() => { if (!cancelled) setFeed((f) => ({ ...f, failed: true })); })
        .finally(() => { if (!cancelled) timer = window.setTimeout(poll, POLL_MS); });
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [pid, enabled]);
  return feed;
}

export function ChatTab({ pid, active }: { pid: number; active: boolean }) {
  const feed = useChat(pid, active);
  const [channel, setChannel] = useState('all');
  const [follow, setFollow] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);

  const rows = channel === 'all' ? feed.messages : feed.messages.filter(m => m.channel === channel);
  const count = (k: string) => feed.messages.reduce((n, m) => n + (m.channel === k ? 1 : 0), 0);
  const chips = [{ k: 'all', n: '全部', c: feed.messages.length }, ...CHANNELS.map(c => ({ ...c, c: count(c.k) }))];

  useLayoutEffect(() => {
    const el = listRef.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [follow, rows.length, channel, active]);

  return (
    <div className="chat">
      <div className="chat-bar">
        <div className="chat-chips" role="group" aria-label="頻道">
          {chips.map(c => (
            <button
              key={c.k} type="button" className="chat-chip" data-ch={c.k}
              aria-pressed={channel === c.k} onClick={() => setChannel(c.k)}
            >
              <span className="chat-chip-n">{c.n}</span><span className="chat-count">{c.c}</span>
            </button>
          ))}
        </div>
        <label className="chat-follow">
          <input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} />
          自動捲到最新
        </label>
      </div>

      <div className="chat-list" ref={listRef} role="log" aria-live="off">
        {rows.length === 0
          ? (
            <div className="chat-empty">
              {feed.failed && feed.messages.length === 0 ? '讀不到聊天紀錄'
                : !feed.connected && feed.messages.length === 0 ? 'Hook 尚未連線'
                  : channel === 'all' ? '還沒有訊息' : '這個頻道還沒有訊息'}
            </div>
          )
          : rows.map(m => (
            <div key={m.seq} className="chat-msg" data-own={m.own || undefined}>
              <span className="chat-time">{clock(m.ts)}</span>
              <span className="chat-ch" data-ch={m.channel}>{CHANNEL_NAME[m.channel] ?? m.channel}</span>
              <span className="chat-name" data-echo={(m.echo && m.channel === 'whisper') || undefined}>
                {m.echo && m.channel === 'whisper' ? `→ ${m.name}` : m.name}
              </span>
              <span className="chat-text">{m.text}</span>
            </div>
          ))}
      </div>

      <div className="chat-foot">
        <span>只收不發 · 從 hook 連線後開始記錄 · 保留最近 {KEEP} 則{!feed.connected && feed.messages.length > 0 ? ' · hook 已斷線' : ''}</span>
        <span className="chat-mono">pipe tthol-hook-{pid}</span>
      </div>
    </div>
  );
}
