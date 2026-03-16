// Canonical list of CSS variable keys -- drives both the TypeScript interface and runtime iteration.
// Adding a key here automatically updates ThemeVariables and CSS_VAR_KEYS.
export const THEME_VAR_KEYS = [
  'background',
  'foreground',
  'card',
  'card-foreground',
  'primary',
  'primary-foreground',
  'secondary',
  'secondary-foreground',
  'muted',
  'muted-foreground',
  'accent',
  'accent-foreground',
  'destructive',
  'destructive-foreground',
  'border',
  'input',
  'ring',
] as const;

export type ThemeVarKey = (typeof THEME_VAR_KEYS)[number];
export type ThemeVariables = Record<ThemeVarKey, string>;

export type ThemeGroup = 'Default' | 'Catppuccin' | 'Dracula' | 'Nord' | 'Gruvbox' | 'Solarized' | 'Other';

export interface ThemePair {
  id: string;
  label: string;
  group: ThemeGroup;
  light: ThemeVariables | null;
  dark: ThemeVariables | null;
  accentColor: string;
}

export const THEME_GROUPS: ThemeGroup[] = [
  'Default',
  'Catppuccin',
  'Dracula',
  'Nord',
  'Gruvbox',
  'Solarized',
  'Other',
];

export const DEFAULT_THEME_ID = 'slate';

// --- Shared bases ---

const catppuccinLatte: ThemeVariables = {
  background: '220 23% 95%',
  foreground: '234 16% 35%',
  card: '220 23% 98%',
  'card-foreground': '234 16% 35%',
  primary: '266 85% 58%',
  'primary-foreground': '220 23% 95%',
  secondary: '223 16% 88%',
  'secondary-foreground': '234 16% 35%',
  muted: '223 16% 88%',
  'muted-foreground': '233 10% 47%',
  accent: '220 22% 92%',
  'accent-foreground': '234 16% 35%',
  destructive: '347 87% 44%',
  'destructive-foreground': '0 0% 100%',
  border: '225 14% 82%',
  input: '225 14% 82%',
  ring: '266 85% 58%',
};

const catppuccinLatteTeal: ThemeVariables = {
  ...catppuccinLatte,
  primary: '183 74% 35%',
  'primary-foreground': '220 23% 95%',
  ring: '183 74% 35%',
};

const catppuccinMochaDark: ThemeVariables = {
  background: '240 21% 15%',
  foreground: '226 64% 88%',
  card: '240 21% 20%',
  'card-foreground': '226 64% 88%',
  primary: '267 84% 81%',
  'primary-foreground': '240 21% 15%',
  secondary: '240 21% 20%',
  'secondary-foreground': '226 64% 88%',
  muted: '237 16% 23%',
  'muted-foreground': '227 35% 72%',
  accent: '240 21% 25%',
  'accent-foreground': '226 64% 88%',
  destructive: '343 81% 75%',
  'destructive-foreground': '240 21% 15%',
  border: '237 16% 23%',
  input: '237 16% 23%',
  ring: '267 84% 81%',
};

const draculaDark: ThemeVariables = {
  background: '231 15% 18%',
  foreground: '60 30% 96%',
  card: '232 14% 22%',
  'card-foreground': '60 30% 96%',
  primary: '265 89% 78%',
  'primary-foreground': '231 15% 18%',
  secondary: '232 14% 22%',
  'secondary-foreground': '60 30% 96%',
  muted: '230 14% 28%',
  'muted-foreground': '228 8% 60%',
  accent: '232 14% 26%',
  'accent-foreground': '60 30% 96%',
  destructive: '0 100% 67%',
  'destructive-foreground': '60 30% 96%',
  border: '230 14% 25%',
  input: '230 14% 25%',
  ring: '265 89% 78%',
};

// --- Theme definitions ---

export const THEMES: ThemePair[] = [
  // --- Default ---
  {
    id: 'slate',
    label: 'Slate',
    group: 'Default',
    light: null,
    dark: null,
    accentColor: '#53b1c3',
  },

  // --- Catppuccin ---
  {
    id: 'catppuccin-mocha',
    label: 'Mocha',
    group: 'Catppuccin',
    light: catppuccinLatte,
    dark: catppuccinMochaDark,
    accentColor: '#cba6f7',
  },
  {
    id: 'catppuccin-mocha-teal',
    label: 'Mocha Teal',
    group: 'Catppuccin',
    light: catppuccinLatteTeal,
    dark: {
      ...catppuccinMochaDark,
      primary: '170 57% 73%',
      'primary-foreground': '240 21% 15%',
      ring: '170 57% 73%',
    },
    accentColor: '#94e2d5',
  },
  {
    id: 'catppuccin-macchiato',
    label: 'Macchiato',
    group: 'Catppuccin',
    light: catppuccinLatte,
    dark: {
      background: '232 23% 18%',
      foreground: '227 68% 88%',
      card: '232 23% 23%',
      'card-foreground': '227 68% 88%',
      primary: '267 83% 80%',
      'primary-foreground': '232 23% 18%',
      secondary: '232 23% 23%',
      'secondary-foreground': '227 68% 88%',
      muted: '230 19% 26%',
      'muted-foreground': '228 39% 73%',
      accent: '232 23% 28%',
      'accent-foreground': '227 68% 88%',
      destructive: '351 74% 73%',
      'destructive-foreground': '232 23% 18%',
      border: '230 19% 26%',
      input: '230 19% 26%',
      ring: '267 83% 80%',
    },
    accentColor: '#c6a0f6',
  },
  {
    id: 'catppuccin-frappe',
    label: 'Frappe',
    group: 'Catppuccin',
    light: catppuccinLatte,
    dark: {
      background: '229 19% 23%',
      foreground: '227 70% 87%',
      card: '229 19% 28%',
      'card-foreground': '227 70% 87%',
      primary: '269 84% 80%',
      'primary-foreground': '229 19% 23%',
      secondary: '229 19% 28%',
      'secondary-foreground': '227 70% 87%',
      muted: '228 17% 31%',
      'muted-foreground': '228 39% 72%',
      accent: '229 19% 33%',
      'accent-foreground': '227 70% 87%',
      destructive: '359 68% 71%',
      'destructive-foreground': '229 19% 23%',
      border: '228 17% 31%',
      input: '228 17% 31%',
      ring: '269 84% 80%',
    },
    accentColor: '#babbf1',
  },

  // --- Dracula ---
  {
    id: 'dracula',
    label: 'Default',
    group: 'Dracula',
    light: null,
    dark: draculaDark,
    accentColor: '#bd93f9',
  },
  {
    id: 'dracula-midnight',
    label: 'Midnight',
    group: 'Dracula',
    light: null,
    dark: {
      ...draculaDark,
      background: '233 15% 13%',
      card: '233 15% 17%',
      'primary-foreground': '233 15% 13%',
      secondary: '233 15% 17%',
      muted: '233 14% 22%',
      'muted-foreground': '228 8% 55%',
      accent: '233 15% 20%',
      border: '233 14% 20%',
      input: '233 14% 20%',
    },
    accentColor: '#bd93f9',
  },
  {
    id: 'dracula-pink',
    label: 'Pink',
    group: 'Dracula',
    light: null,
    dark: { ...draculaDark, primary: '326 100% 74%', ring: '326 100% 74%' },
    accentColor: '#ff79c6',
  },
  {
    id: 'dracula-cyan',
    label: 'Cyan',
    group: 'Dracula',
    light: null,
    dark: { ...draculaDark, primary: '191 97% 77%', ring: '191 97% 77%' },
    accentColor: '#8be9fd',
  },
  {
    id: 'dracula-green',
    label: 'Green',
    group: 'Dracula',
    light: null,
    dark: { ...draculaDark, primary: '135 94% 65%', ring: '135 94% 65%' },
    accentColor: '#50fa7b',
  },
  {
    id: 'dracula-orange',
    label: 'Orange',
    group: 'Dracula',
    light: null,
    dark: { ...draculaDark, primary: '31 100% 71%', ring: '31 100% 71%' },
    accentColor: '#ffb86c',
  },

  // --- Nord ---
  {
    id: 'nord',
    label: 'Nord',
    group: 'Nord',
    light: {
      background: '219 28% 94%',
      foreground: '220 16% 22%',
      card: '219 28% 98%',
      'card-foreground': '220 16% 22%',
      primary: '213 32% 52%',
      'primary-foreground': '0 0% 100%',
      secondary: '219 28% 88%',
      'secondary-foreground': '220 16% 22%',
      muted: '219 28% 88%',
      'muted-foreground': '220 16% 46%',
      accent: '219 28% 91%',
      'accent-foreground': '220 16% 22%',
      destructive: '354 42% 56%',
      'destructive-foreground': '0 0% 100%',
      border: '220 17% 82%',
      input: '220 17% 82%',
      ring: '213 32% 52%',
    },
    dark: {
      background: '220 16% 22%',
      foreground: '219 28% 88%',
      card: '222 16% 28%',
      'card-foreground': '219 28% 88%',
      primary: '213 32% 52%',
      'primary-foreground': '0 0% 100%',
      secondary: '222 16% 28%',
      'secondary-foreground': '219 28% 88%',
      muted: '220 17% 32%',
      'muted-foreground': '219 14% 60%',
      accent: '222 16% 32%',
      'accent-foreground': '219 28% 88%',
      destructive: '354 42% 56%',
      'destructive-foreground': '0 0% 100%',
      border: '220 17% 27%',
      input: '220 17% 27%',
      ring: '213 32% 52%',
    },
    accentColor: '#5e81ac',
  },

  // --- Gruvbox ---
  {
    id: 'gruvbox',
    label: 'Gruvbox',
    group: 'Gruvbox',
    light: {
      background: '44 87% 94%',
      foreground: '0 0% 16%',
      card: '47 80% 90%',
      'card-foreground': '0 0% 16%',
      primary: '24 88% 45%',
      'primary-foreground': '44 87% 94%',
      secondary: '44 60% 84%',
      'secondary-foreground': '0 0% 16%',
      muted: '44 60% 84%',
      'muted-foreground': '0 0% 36%',
      accent: '44 70% 88%',
      'accent-foreground': '0 0% 16%',
      destructive: '6 96% 30%',
      'destructive-foreground': '44 87% 94%',
      border: '42 38% 76%',
      input: '42 38% 76%',
      ring: '24 88% 45%',
    },
    dark: {
      background: '0 0% 16%',
      foreground: '42 46% 81%',
      card: '20 5% 22%',
      'card-foreground': '42 46% 81%',
      primary: '27 99% 55%',
      'primary-foreground': '0 0% 16%',
      secondary: '20 5% 22%',
      'secondary-foreground': '42 46% 81%',
      muted: '21 6% 28%',
      'muted-foreground': '34 22% 55%',
      accent: '20 5% 26%',
      'accent-foreground': '42 46% 81%',
      destructive: '6 96% 59%',
      'destructive-foreground': '0 0% 16%',
      border: '20 5% 25%',
      input: '20 5% 25%',
      ring: '27 99% 55%',
    },
    accentColor: '#d65d0e',
  },

  // --- Solarized ---
  {
    id: 'solarized',
    label: 'Solarized',
    group: 'Solarized',
    light: {
      background: '44 87% 94%',
      foreground: '192 81% 14%',
      card: '44 87% 98%',
      'card-foreground': '192 81% 14%',
      primary: '205 70% 48%',
      'primary-foreground': '44 87% 94%',
      secondary: '46 42% 86%',
      'secondary-foreground': '192 81% 14%',
      muted: '46 42% 86%',
      'muted-foreground': '194 14% 40%',
      accent: '46 42% 90%',
      'accent-foreground': '192 81% 14%',
      destructive: '1 71% 52%',
      'destructive-foreground': '44 87% 94%',
      border: '46 42% 80%',
      input: '46 42% 80%',
      ring: '205 70% 48%',
    },
    dark: {
      background: '192 81% 14%',
      foreground: '44 87% 94%',
      card: '192 100% 11%',
      'card-foreground': '44 87% 94%',
      primary: '205 70% 48%',
      'primary-foreground': '192 81% 14%',
      secondary: '192 100% 11%',
      'secondary-foreground': '44 87% 94%',
      muted: '194 14% 20%',
      'muted-foreground': '180 7% 56%',
      accent: '192 49% 18%',
      'accent-foreground': '44 87% 94%',
      destructive: '1 71% 52%',
      'destructive-foreground': '44 87% 94%',
      border: '194 14% 20%',
      input: '194 14% 20%',
      ring: '205 70% 48%',
    },
    accentColor: '#268bd2',
  },

  // --- Other ---
  {
    id: 'tokyo-night',
    label: 'Tokyo Night',
    group: 'Other',
    light: null,
    dark: {
      background: '235 18% 14%',
      foreground: '226 63% 82%',
      card: '235 18% 19%',
      'card-foreground': '226 63% 82%',
      primary: '230 94% 82%',
      'primary-foreground': '235 18% 14%',
      secondary: '235 18% 19%',
      'secondary-foreground': '226 63% 82%',
      muted: '234 16% 23%',
      'muted-foreground': '228 20% 55%',
      accent: '235 18% 24%',
      'accent-foreground': '226 63% 82%',
      destructive: '348 86% 61%',
      'destructive-foreground': '0 0% 100%',
      border: '234 16% 22%',
      input: '234 16% 22%',
      ring: '230 94% 82%',
    },
    accentColor: '#7aa2f7',
  },
  {
    id: 'github-dark',
    label: 'GitHub Dark',
    group: 'Other',
    light: null,
    dark: {
      background: '215 21% 11%',
      foreground: '213 14% 77%',
      card: '216 18% 16%',
      'card-foreground': '213 14% 77%',
      primary: '212 92% 66%',
      'primary-foreground': '0 0% 100%',
      secondary: '216 18% 16%',
      'secondary-foreground': '213 14% 77%',
      muted: '215 14% 21%',
      'muted-foreground': '213 10% 52%',
      accent: '216 18% 20%',
      'accent-foreground': '213 14% 77%',
      destructive: '0 72% 51%',
      'destructive-foreground': '0 0% 100%',
      border: '215 14% 19%',
      input: '215 14% 19%',
      ring: '212 92% 66%',
    },
    accentColor: '#58a6ff',
  },
  {
    id: 'unifi',
    label: 'UniFi',
    group: 'Other',
    light: {
      background: '220 14% 96%',
      foreground: '220 9% 18%',
      card: '0 0% 100%',
      'card-foreground': '220 9% 18%',
      primary: '216 98% 52%',
      'primary-foreground': '0 0% 100%',
      secondary: '218 14% 90%',
      'secondary-foreground': '220 9% 18%',
      muted: '218 14% 90%',
      'muted-foreground': '220 9% 44%',
      accent: '218 14% 93%',
      'accent-foreground': '220 9% 18%',
      destructive: '4 90% 58%',
      'destructive-foreground': '0 0% 100%',
      border: '218 14% 84%',
      input: '218 14% 84%',
      ring: '216 98% 52%',
    },
    dark: {
      background: '225 6% 13%',
      foreground: '220 9% 82%',
      card: '225 6% 17%',
      'card-foreground': '220 9% 82%',
      primary: '216 98% 52%',
      'primary-foreground': '0 0% 100%',
      secondary: '225 6% 17%',
      'secondary-foreground': '220 9% 82%',
      muted: '225 6% 22%',
      'muted-foreground': '220 9% 55%',
      accent: '225 6% 22%',
      'accent-foreground': '220 9% 82%',
      destructive: '4 90% 58%',
      'destructive-foreground': '0 0% 100%',
      border: '225 6% 20%',
      input: '225 6% 20%',
      ring: '216 98% 52%',
    },
    accentColor: '#0559f0',
  },
  {
    id: 'blue-slate',
    label: 'Blue Slate',
    group: 'Other',
    light: null,
    dark: {
      background: '222 47% 11%',
      foreground: '213 31% 91%',
      card: '217 33% 17%',
      'card-foreground': '213 31% 91%',
      primary: '210 100% 66%',
      'primary-foreground': '222 47% 11%',
      secondary: '217 33% 17%',
      'secondary-foreground': '213 31% 91%',
      muted: '215 28% 22%',
      'muted-foreground': '217 19% 55%',
      accent: '217 33% 22%',
      'accent-foreground': '213 31% 91%',
      destructive: '0 63% 55%',
      'destructive-foreground': '0 0% 100%',
      border: '215 28% 20%',
      input: '215 28% 20%',
      ring: '210 100% 66%',
    },
    accentColor: '#5196ff',
  },
];

export function getThemeById(id: string): ThemePair | undefined {
  return THEMES.find((t) => t.id === id);
}

// Pre-computed grouped themes for rendering (avoids re-filtering on every render)
export const GROUPED_THEMES: ReadonlyMap<ThemeGroup, ThemePair[]> = new Map(
  THEME_GROUPS.map((g) => [g, THEMES.filter((t) => t.group === g)])
);

// Module-load validation: ensure DEFAULT_THEME_ID exists and every theme is in a group
if (import.meta.env.DEV) {
  if (!getThemeById(DEFAULT_THEME_ID)) {
    console.error(`[themes] DEFAULT_THEME_ID '${DEFAULT_THEME_ID}' not found in THEMES`);
  }
  const grouped = new Set(THEME_GROUPS);
  for (const t of THEMES) {
    if (!grouped.has(t.group)) {
      console.error(`[themes] Theme '${t.id}' has group '${t.group}' not in THEME_GROUPS`);
    }
  }
  const ids = THEMES.map((t) => t.id);
  const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
  if (dupes.length) {
    console.error(`[themes] Duplicate theme IDs: ${dupes.join(', ')}`);
  }
}
