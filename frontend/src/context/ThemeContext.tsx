import { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { DEFAULT_THEME_ID, getThemeById, applyThemeVariables, clearThemeVariables } from '../themes';

type Theme = 'light' | 'dark';

interface ThemeContextType {
  theme: Theme;
  toggleTheme: () => void;
  themeId: string;
  setThemeId: (id: string) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem('theme');
    if (stored === 'light' || stored === 'dark') {
      return stored;
    }
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });

  const [themeId, setThemeId] = useState<string>(() => {
    return localStorage.getItem('themeId') || DEFAULT_THEME_ID;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    localStorage.setItem('theme', theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem('themeId', themeId);
  }, [themeId]);

  useEffect(() => {
    const pair = getThemeById(themeId) ?? getThemeById(DEFAULT_THEME_ID);
    if (!pair) {
      clearThemeVariables();
      return;
    }
    const vars = theme === 'dark' ? pair.dark : pair.light;

    if (vars) {
      applyThemeVariables(vars);
    } else {
      clearThemeVariables();
    }
  }, [theme, themeId]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  };

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, themeId, setThemeId }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
}
