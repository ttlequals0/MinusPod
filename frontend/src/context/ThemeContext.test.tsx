import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ThemeProvider } from './ThemeContext';

describe('ThemeProvider theme-color', () => {
  beforeEach(() => {
    localStorage.setItem('theme', 'dark');
    localStorage.setItem('themeId', 'catppuccin-mocha');
    document.head.innerHTML = '<meta name="theme-color" content="#2ea8c7" />';
  });
  afterEach(() => { localStorage.clear(); document.head.innerHTML = ''; document.documentElement.removeAttribute('style'); });

  it('follows the active theme background', () => {
    render(<ThemeProvider><div /></ThemeProvider>);
    const bg = document.documentElement.style.getPropertyValue('--background');
    expect(bg).not.toBe('');
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe(`hsl(${bg})`);
  });
});
