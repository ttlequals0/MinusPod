import { useState, useEffect } from 'react';

export interface ThemeColors {
  primary: string;
  card: string;
  border: string;
  foreground: string;
  muted: string;
}

/**
 * Resolve theme CSS variables to hsl() strings for recharts (which needs
 * concrete colors, not CSS vars) and re-resolve them on theme switch.
 */
// Slate light values from index.css, so a chart still renders before the
// variables resolve instead of falling back to an off-palette hardcoded hex.
const DEFAULTS: ThemeColors = {
  primary: 'hsl(194 66% 45%)',
  card: 'hsl(0 0% 100%)',
  border: 'hsl(210 15% 85%)',
  foreground: 'hsl(213 10% 17%)',
  muted: 'hsl(210 5% 45%)',
};

export function useThemeColors(): ThemeColors {
  const [colors, setColors] = useState<ThemeColors>(DEFAULTS);
  useEffect(() => {
    function resolve(name: string, fallback: string) {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return raw ? `hsl(${raw})` : fallback;
    }
    function update() {
      const next = {
        primary: resolve('--primary', DEFAULTS.primary),
        card: resolve('--card', DEFAULTS.card),
        border: resolve('--border', DEFAULTS.border),
        foreground: resolve('--card-foreground', DEFAULTS.foreground),
        muted: resolve('--muted-foreground', DEFAULTS.muted),
      };
      setColors(prev =>
        prev.primary === next.primary && prev.card === next.card && prev.border === next.border
        && prev.foreground === next.foreground && prev.muted === next.muted
          ? prev : next
      );
    }
    update();
    const obs = new MutationObserver(update);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
    return () => obs.disconnect();
  }, []);
  return colors;
}
