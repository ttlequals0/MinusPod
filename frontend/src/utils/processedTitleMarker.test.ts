import { describe, it, expect } from 'vitest';
import { markerProblem } from './processedTitleMarker';

describe('markerProblem', () => {
  it('accepts blank (off) and ordinary short markers', () => {
    for (const ok of ['', '[a] ', '\u2705 ', '(cleaned) ']) expect(markerProblem(ok)).toBeNull();
  });

  it('says plainly when the marker is only spaces', () => {
    for (const blank of [' ', '   ', '\u00a0', '\u3000']) {
      expect(markerProblem(blank)).toBe("The marker can't be only spaces.");
    }
  });

  it('names the hidden character it found', () => {
    const cases: Array<[string, string]> = [
      ['[a]\u0085', 'U+0085'], ['[a]\u200b', 'U+200B'], ['[a]\u2028', 'U+2028'],
      ['[a]\u2029', 'U+2029'], ['[a]\u202e', 'U+202E'], ['\ufeff[a]', 'U+FEFF'], ['[a]\u0007', 'U+0007'],
    ];
    for (const [bad, code] of cases) {
      expect(markerProblem(bad)).toBe(`The marker can't contain a hidden or control character (${code}).`);
    }
  });

  it('counts characters (code points), the same unit as the API', () => {
    expect(markerProblem('x'.repeat(17))).toBe('The marker can be at most 16 characters.');
    expect(markerProblem('x'.repeat(16))).toBeNull();
    expect(markerProblem('\u{1F600}'.repeat(16))).toBeNull();          // 32 UTF-16 units, 16 characters
    expect(markerProblem('\u{1F600}'.repeat(17))).toBe('The marker can be at most 16 characters.');
  });

  it('never exposes the API field name', () => {
    for (const v of [' ', 'x'.repeat(17), '[a]\u200b']) {
      expect(markerProblem(v)).not.toMatch(/processedTitleMarker/);
    }
  });
});
