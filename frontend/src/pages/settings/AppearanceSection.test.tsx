import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ThemeProvider } from '../../context/ThemeContext';
import AppearanceSection from './AppearanceSection';

function badge(label: string) {
  return screen.getByRole('button', { name: new RegExp(`^${label}`) }).textContent;
}

describe('AppearanceSection mode badges', () => {
  it('labels single-mode themes and leaves pairs and the default alone', () => {
    render(<ThemeProvider><AppearanceSection /></ThemeProvider>);
    expect(badge('Slate')).toBe('Slate');
    expect(badge('Nord')).toBe('Nord');
    expect(badge('Obsidian')).toContain('(dark only)');
    expect(badge('Harbor')).toContain('(light only)');
  });

  it('renders a 3-swatch palette preview for paired, single-mode, and default themes', () => {
    render(<ThemeProvider><AppearanceSection /></ThemeProvider>);
    for (const id of ['slate', 'nord', 'obsidian', 'harbor']) {
      const swatch = screen.getByTestId(`theme-swatch-${id}`);
      const bars = swatch.querySelectorAll('span');
      expect(bars).toHaveLength(3);
      for (const bar of bars) {
        expect((bar as HTMLElement).style.backgroundColor).toBeTruthy();
      }
    }
  });
});
