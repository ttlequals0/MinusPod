import { describe, it, expect } from 'vitest';
import { formatConfidence, formatConfidenceTitle } from './confidence';

describe('confidence tooltip', () => {
  it('shows one number when review changed nothing', () => {
    const seg = { confidence: 0.9, validation: { adjusted_confidence: 0.9 } } as never;
    expect(formatConfidence(seg)).toBe('90% confidence');
    expect(formatConfidenceTitle(seg)).toBeUndefined();
  });

  it('keeps the raw / adjusted text and adds a tooltip naming both numbers', () => {
    const seg = { confidence: 0.8, validation: { adjusted_confidence: 1 } } as never;
    expect(formatConfidence(seg)).toBe('80% raw / 100% adjusted');
    expect(formatConfidenceTitle(seg)).toBe('Model confidence 80%, after review 100%');
  });
});
