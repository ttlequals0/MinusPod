export type { ThemeVariables, ThemeVarKey, ThemePair, ThemeGroup } from './themes';
export { THEMES, THEME_VAR_KEYS, THEME_GROUPS, GROUPED_THEMES, DEFAULT_THEME_ID, getThemeById } from './themes';

import type { ThemeVariables } from './themes';
import { THEME_VAR_KEYS } from './themes';

export function applyThemeVariables(vars: ThemeVariables): void {
  const style = document.documentElement.style;
  for (const key of THEME_VAR_KEYS) {
    style.setProperty(`--${key}`, vars[key]);
  }
}

export function clearThemeVariables(): void {
  const style = document.documentElement.style;
  for (const key of THEME_VAR_KEYS) {
    style.removeProperty(`--${key}`);
  }
}
