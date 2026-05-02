export type ThemeName = '暗紅' | '暗金' | '水墨青';
export type FontName = '黑體' | '中文襯線';

export interface ThemeTokens {
  bg: string; bgPanel: string; bgRaised: string;
  line: string; lineSoft: string;
  text: string; textDim: string; textMute: string;
  accent: string; accentDim: string;
  gold: string; ok: string; warn: string; bad: string; seal: string; grid: string;
}

export const THEMES: Record<ThemeName, ThemeTokens> = {
  '暗紅': {
    bg: '#14100e', bgPanel: '#1c1815', bgRaised: '#241e1a',
    line: '#3a2f28', lineSoft: '#2a221d',
    text: '#e8dcc8', textDim: '#a89880', textMute: '#6b5e4e',
    accent: '#c83838', accentDim: '#8a2828',
    gold: '#c9a866', ok: '#7ca858', warn: '#d4a142', bad: '#c83838',
    seal: '#a02828', grid: 'rgba(200,56,56,.05)',
  },
  '暗金': {
    bg: '#0f0d0a', bgPanel: '#181410', bgRaised: '#221c16',
    line: '#3d3528', lineSoft: '#28221a',
    text: '#e8d9b0', textDim: '#9c8a68', textMute: '#6b5e44',
    accent: '#c9a866', accentDim: '#8a7440',
    gold: '#e8c878', ok: '#a8b888', warn: '#d4a142', bad: '#b85838',
    seal: '#8a2828', grid: 'rgba(201,168,102,.05)',
  },
  '水墨青': {
    bg: '#0c1014', bgPanel: '#141a20', bgRaised: '#1c242c',
    line: '#2c3a44', lineSoft: '#1f2830',
    text: '#d8e2e8', textDim: '#8a9aa4', textMute: '#5a6a74',
    accent: '#5a8898', accentDim: '#3a5868',
    gold: '#c9b884', ok: '#7ca898', warn: '#d4b072', bad: '#c87878',
    seal: '#a04848', grid: 'rgba(90,136,152,.05)',
  },
};

export const FONT_STACKS: Record<FontName, string> = {
  '中文襯線':
    '"Noto Serif TC", "Source Han Serif TC", "Songti TC", "PMingLiU", serif',
  '黑體':
    '"Noto Sans TC", "Source Han Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif',
};
