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
export function useThemeColors(): ThemeColors {
  const [colors, setColors] = useState<ThemeColors>({
    primary: '', card: '', border: '', foreground: '', muted: '',
  });
  useEffect(() => {
    function resolve(name: string) {
      const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return raw ? `hsl(${raw})` : '';
    }
    function update() {
      const next = {
        primary: resolve('--primary'),
        card: resolve('--card'),
        border: resolve('--border'),
        foreground: resolve('--card-foreground'),
        muted: resolve('--muted-foreground'),
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
