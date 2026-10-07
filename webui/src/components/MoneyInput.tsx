import { useState } from 'react';
import './money.css';

/** Most 銀兩 a character carries; a warehouse holds one digit more. */
export const CARRY_MAX = 99_999_999;
export const BANK_MAX = 999_999_999;

const UNITS: Record<string, number> = { '億': 1e8, '萬': 1e4, 'w': 1e4, 'k': 1e3 };

/** "12,345,678" / "1234萬" / "1.5億" / "300w" → an integer; null when not a number. */
export function parseMoney(text: string): number | null {
  const s = text.replace(/[,\s]/g, '').toLowerCase();
  if (s === '') return 0;
  const m = /^(\d+(?:\.\d*)?)(億|萬|w|k)?$/.exec(s);
  if (!m) return null;
  return Math.floor(Number(m[1]) * (m[2] ? UNITS[m[2]] : 1));
}

/** 123456789 → "1億2345萬6789"; 50000000 → "5000萬"; 100000001 → "1億零1". */
export function readMoney(n: number): string {
  if (n < 1e4) return String(n);
  const yi = Math.floor(n / 1e8);
  const wan = Math.floor((n % 1e8) / 1e4);
  const rest = n % 1e4;
  let s = yi ? `${yi}億` : '';
  if (wan) s += (yi && wan < 1000 ? '零' : '') + `${wan}萬`;
  if (rest) s += ((yi || wan) && (rest < 1000 || !wan) ? '零' : '') + rest;
  return s;
}

const commas = (n: number) => n.toLocaleString('en-US');

/**
 * A 銀兩 field. Unfocused it shows the amount with commas; focused it holds the
 * plain digits, all selected, and reads them back in 萬 / 億 underneath so the
 * zeros need no counting. Typing 300萬, 1.5億 or 300w works too.
 */
export function MoneyInput({ value, onChange, max = CARRY_MAX, disabled, className, ...rest }: {
  value: number;
  onChange: (n: number) => void;
  max?: number;
  disabled?: boolean;
  className?: string;
  'aria-label'?: string;
  id?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const parsed = draft == null ? value : parseMoney(draft);
  const clamp = (n: number) => Math.min(max, Math.max(0, n));
  const over = parsed != null && parsed > max;

  return (
    <span className="money">
      <input
        {...rest}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        data-select-all
        disabled={disabled}
        className={`money-input${className ? ` ${className}` : ''}`}
        style={{ width: `calc(${commas(max).length}ch + 18px)` }}
        aria-invalid={draft != null && parsed == null}
        value={draft ?? commas(value)}
        onFocus={e => {
          // Swapping "1,234" for "1234" drops the selection; select again after it.
          const el = e.currentTarget;
          setDraft(value ? String(value) : '');
          requestAnimationFrame(() => { if (document.activeElement === el) el.select(); });
        }}
        onChange={e => {
          setDraft(e.target.value);
          const n = parseMoney(e.target.value);
          if (n != null) onChange(clamp(n));
        }}
        onBlur={() => setDraft(null)}
        onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur(); }}
      />
      {draft != null && (
        <span className={`money-read${parsed == null || over ? ' is-bad' : ''}`} aria-live="polite">
          {parsed == null ? '看不懂這個數字' : over ? `最多 ${readMoney(max)}` : readMoney(parsed)}
        </span>
      )}
    </span>
  );
}
