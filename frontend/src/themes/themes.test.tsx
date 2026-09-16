/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { THEMES, THEME_VAR_KEYS } from './themes';

// A badge is a 20 percent fill of its hue, 30 percent while hovered, and it may
// sit inside a 10 percent panel of the same hue. Every pairing has to clear AA.
const FILLS = [0.2, 0.3];
const PANELS = [0, 0.1];
const MIN_RATIO = 4.5;

type Rgb = [number, number, number];

/** Parses an `H S% L%` variable value into linear-free sRGB channels (0-1). */
function hslToRgb(triplet: string): Rgb {
  const [h, s, l] = triplet.trim().split(/\s+/).map((v) => parseFloat(v));
  const sat = s / 100;
  const lig = l / 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = sat * Math.min(lig, 1 - lig);
  const f = (n: number) => lig - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [f(0), f(8), f(4)];
}

function luminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const composite = (fg: Rgb, bg: Rgb, alpha: number): Rgb =>
  fg.map((c, i) => c * alpha + bg[i] * (1 - alpha)) as Rgb;

// A theme with a null mode inherits that mode's variables from index.css.
// Read off disk, relative to the vitest root: a CSS import comes back empty.
const css = readFileSync(`${process.cwd()}/src/index.css`, 'utf8');
function cssVars(selector: string): Record<string, string> {
  const block = css.slice(css.indexOf(`${selector} {`));
  const body = block.slice(block.indexOf('{') + 1, block.indexOf('}'));
  return Object.fromEntries(
    [...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
  );
}
const DEFAULTS = { light: cssVars(':root'), dark: cssVars('.dark') };

// c-blue/c-purple/c-teal are mode-driven only, so their hue and text always come
// from index.css even when the surfaces under them are a theme's own.
const CSS_HUES = ['c-blue', 'c-purple', 'c-teal'] as const;
const THEME_HUES = THEME_VAR_KEYS.filter((k) => k.endsWith('-on-tint'))
  .map((k) => k.replace('-on-tint', ''));
// Chips with a solid fill instead of a tint.
const SOLID: [string, string][] = [
  ['muted', 'secondary-foreground'],
  ['secondary', 'secondary-foreground'],
];

type Mode = 'light' | 'dark';

function failures(vars: Record<string, string>, mode: Mode): string[] {
  const out: string[] = [];
  const check = (label: string, text: Rgb, bg: Rgb) => {
    const ratio = contrast(text, bg);
    if (ratio < MIN_RATIO) out.push(`${label}: ${ratio.toFixed(2)}`);
  };
  const tints: [string, Record<string, string>][] = [
    ...CSS_HUES.map((h) => [h, DEFAULTS[mode]] as [string, Record<string, string>]),
    ...THEME_HUES.map((h) => [h, vars] as [string, Record<string, string>]),
  ];
  for (const [name, source] of tints) {
    const hue = hslToRgb(source[name]);
    const text = hslToRgb(source[`${name}-on-tint`]);
    for (const surface of ['card', 'background'] as const) {
      const base = hslToRgb(vars[surface]);
      for (const panel of PANELS) {
        const under = panel ? composite(hue, base, panel) : base;
        for (const fill of FILLS) {
          const where = `${surface}${panel ? '+panel10' : ''} at ${fill * 100}%`;
          check(`${name}-on-tint on ${where}`, text, composite(hue, under, fill));
        }
      }
    }
  }
  for (const [fill, text] of SOLID) {
    check(`${text} on ${fill}`, hslToRgb(vars[text]), hslToRgb(vars[fill]));
  }
  return out;
}

describe('theme on-tint tokens clear WCAG AA', () => {
  for (const theme of THEMES) {
    for (const mode of ['light', 'dark'] as const) {
      it(`${theme.id} ${mode}`, () => {
        expect(failures(theme[mode] ?? DEFAULTS[mode], mode)).toEqual([]);
      });
    }
  }

  it('covers the index.css defaults themselves', () => {
    expect(failures(DEFAULTS.light, 'light')).toEqual([]);
    expect(failures(DEFAULTS.dark, 'dark')).toEqual([]);
  });
});
