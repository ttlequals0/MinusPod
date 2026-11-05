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

  it('rejects control, invisible and separator characters in plain words', () => {
    for (const bad of ['[a]\u0085', '[a]\u200b', '[a]\u2028', '[a]\u2029', '[a]\u202e', '\ufeff[a]', '[a]\u0007']) {
      expect(markerProblem(bad)).toBe("The marker can't contain hidden or control characters.");
    }
  });

  it('rejects a marker over 16 characters', () => {
    expect(markerProblem('x'.repeat(17))).toBe('The marker can be at most 16 characters.');
    expect(markerProblem('x'.repeat(16))).toBeNull();
  });

  it('never exposes the API field name', () => {
    for (const v of [' ', 'x'.repeat(17), '[a]\u200b']) {
      expect(markerProblem(v)).not.toMatch(/processedTitleMarker/);
    }
  });
});
