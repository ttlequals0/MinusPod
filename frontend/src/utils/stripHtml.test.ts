import { describe, expect, it } from 'vitest';
import { stripHtml } from './stripHtml';

describe('stripHtml', () => {
  it('strips tags by default', () => {
    expect(stripHtml('<p>A</p><p>B</p>')).toBe('AB');
  });

  it('leaves full-text whitespace unchanged by default', () => {
    expect(stripHtml('A\n\nB')).toBe('A\n\nB');
  });

  it('collapses whitespace runs for compact previews', () => {
    expect(stripHtml('A\n\n\n   B', { collapse: true })).toBe('A B');
  });

  it('returns empty string for null/undefined input', () => {
    expect(stripHtml(null)).toBe('');
    expect(stripHtml(undefined)).toBe('');
  });
});
