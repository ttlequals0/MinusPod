import { describe, expect, it } from 'vitest';
import { pageWindow } from './Pagination';

const w = (page: number, total: number, n: number) =>
  pageWindow(page, total, n).map((p) => (p === 'ellipsis' ? '...' : p)).join(' ');

describe('pageWindow', () => {
  it('shows every page when the total fits', () => {
    expect(w(1, 1, 0)).toBe('1');
    expect(w(2, 3, 0)).toBe('1 2 3');
    expect(w(1, 5, 1)).toBe('1 2 3 4 5');
  });

  it('never replaces exactly one page with an ellipsis', () => {
    expect(w(2, 4, 0)).toBe('1 2 3 4');
    expect(w(3, 4, 0)).toBe('1 2 3 4');
    expect(w(3, 5, 0)).toBe('1 2 3 4 5');
  });

  it('collapses long ranges for the narrow window (neighbours 0)', () => {
    expect(w(1, 8, 0)).toBe('1 ... 8');
    expect(w(4, 8, 0)).toBe('1 ... 4 ... 8');
    expect(w(8, 8, 0)).toBe('1 ... 8');
  });

  it('collapses long ranges for the desktop window (neighbours 1)', () => {
    expect(w(1, 8, 1)).toBe('1 2 ... 8');
    expect(w(4, 8, 1)).toBe('1 2 3 4 5 ... 8');
    expect(w(5, 10, 1)).toBe('1 ... 4 5 6 ... 10');
    expect(w(8, 8, 1)).toBe('1 ... 7 8');
  });

  it('always includes first, last and current, with no single-page ellipsis', () => {
    for (const n of [0, 1]) {
      for (let total = 1; total <= 12; total++) {
        for (let page = 1; page <= total; page++) {
          const out = pageWindow(page, total, n);
          expect(out).toContain(1);
          expect(out).toContain(total);
          expect(out).toContain(page);
          out.forEach((p, i) => {
            if (p !== 'ellipsis') return;
            const gap = (out[i + 1] as number) - (out[i - 1] as number) - 1;
            expect(gap).toBeGreaterThan(1);
          });
        }
      }
    }
  });
});
