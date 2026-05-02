import { jsx as _jsx } from "react/jsx-runtime";
import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { THEMES, FONT_STACKS } from './tokens';
const Ctx = createContext(null);
const KEY = 'tthol-tweaks';
function loadTweaks() {
    try {
        return JSON.parse(localStorage.getItem(KEY) || '{}');
    }
    catch {
        return {};
    }
}
export function ThemeProvider({ children }) {
    const initial = loadTweaks();
    const [theme, setTheme] = useState(initial.theme ?? '暗紅');
    const [font, setFont] = useState(initial.font ?? '黑體');
    const [density, setDensity] = useState(initial.density ?? 'normal');
    const [chipMode, setChipMode] = useState(initial.chipMode ?? 'avatar');
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
        root.style.setProperty('--tt-font', FONT_STACKS[font]);
        root.style.setProperty('--tt-font-serif', FONT_STACKS['中文襯線']);
        root.style.setProperty('--tt-font-mono', '"JetBrains Mono", "IBM Plex Mono", ui-monospace, monospace');
        document.body.style.fontFamily = FONT_STACKS[font];
    }, [theme, font, density, chipMode]);
    const value = useMemo(() => ({ theme, font, density, chipMode, setTheme, setFont, setDensity, setChipMode }), [theme, font, density, chipMode]);
    return _jsx(Ctx.Provider, { value: value, children: children });
}
export function useTheme() {
    const v = useContext(Ctx);
    if (!v)
        throw new Error('useTheme requires <ThemeProvider>');
    return v;
}
