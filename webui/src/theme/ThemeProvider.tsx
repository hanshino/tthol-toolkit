import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { THEMES, FONT_STACKS, type ThemeName, type FontName } from './tokens';

export type Density = 'compact' | 'normal' | 'comfy';
export type ChipMode = 'avatar' | 'text' | 'number';

interface ThemeState {
  theme: ThemeName;
  font: FontName;
  density: Density;
  chipMode: ChipMode;
  setTheme: (t: ThemeName) => void;
  setFont: (f: FontName) => void;
  setDensity: (d: Density) => void;
  setChipMode: (c: ChipMode) => void;
}

const Ctx = createContext<ThemeState | null>(null);

const KEY = 'tthol-tweaks';

function loadTweaks() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}');
  } catch {
    return {};
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const initial = loadTweaks();
  const [theme, setTheme] = useState<ThemeName>(initial.theme ?? '暗紅');
  const [font, setFont] = useState<FontName>(initial.font ?? '黑體');
  const [density, setDensity] = useState<Density>(initial.density ?? 'normal');
  const [chipMode, setChipMode] = useState<ChipMode>(initial.chipMode ?? 'avatar');

  useEffect(() => {
    localStorage.setItem(KEY, JSON.stringify({ theme, font, density, chipMode }));
    const t = THEMES[theme];
    const root = document.documentElement;
    root.style.setProperty('--tt-bg', t.bg);
    root.style.setProperty('--tt-panel', t.bgPanel);
    root.style.setProperty('--tt-raised', t.bgRaised);
    root.style.setProperty('--tt-line', t.line);
    root.style.setProperty('--tt-line-soft', t.lineSoft);
    root.style.setProperty('--tt-text', t.text);
    root.style.setProperty('--tt-dim', t.textDim);
    root.style.setProperty('--tt-mute', t.textMute);
    root.style.setProperty('--tt-accent', t.accent);
    root.style.setProperty('--tt-accent-dim', t.accentDim);
    root.style.setProperty('--tt-gold', t.gold);
    root.style.setProperty('--tt-ok', t.ok);
    root.style.setProperty('--tt-warn', t.warn);
    root.style.setProperty('--tt-bad', t.bad);
    root.style.setProperty('--tt-seal', t.seal);
    root.style.setProperty('--tt-grid', t.grid);
    root.style.setProperty('--tt-font', FONT_STACKS[font]);
    root.style.setProperty('--tt-font-serif', FONT_STACKS['中文襯線']);
    root.style.setProperty('--tt-font-mono',
      '"JetBrains Mono", "IBM Plex Mono", ui-monospace, monospace');
    document.body.style.fontFamily = FONT_STACKS[font];
  }, [theme, font, density, chipMode]);

  const value = useMemo(
    () => ({ theme, font, density, chipMode, setTheme, setFont, setDensity, setChipMode }),
    [theme, font, density, chipMode],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useTheme requires <ThemeProvider>');
  return v;
}
