import { describe, expect, it } from 'vitest';
import { confirmLabel, rejectLabel } from './segmentCategory';

describe('confirmLabel', () => {
  it('calls a sponsor or uncategorized marker an ad', () => {
    expect(confirmLabel('sponsor')).toBe('Confirm');
    expect(confirmLabel(undefined)).toBe('Confirm');
    expect(confirmLabel(null)).toBe('Confirm');
    expect(confirmLabel('')).toBe('Confirm');
  });

  it('names any other category instead of calling it an ad', () => {
    expect(confirmLabel('interaction')).toBe('Confirm interaction');
    expect(confirmLabel('self_promo')).toBe('Confirm self-promo');
    expect(confirmLabel('cross_promo')).toBe('Confirm cross-promo');
    expect(confirmLabel('intro')).toBe('Confirm intro');
  });

  it('falls back to ad for a category the frontend does not know', () => {
    expect(confirmLabel('something_new')).toBe('Confirm');
  });
});

describe('rejectLabel', () => {
  it('reads Not ad for sponsor and uncategorized markers', () => {
    expect(rejectLabel('sponsor')).toBe('Not ad');
    expect(rejectLabel(undefined)).toBe('Not ad');
    expect(rejectLabel(null)).toBe('Not ad');
    expect(rejectLabel('')).toBe('Not ad');
  });

  it('reads Keep for any other known category', () => {
    expect(rejectLabel('interaction')).toBe('Keep');
    expect(rejectLabel('self_promo')).toBe('Keep');
    expect(rejectLabel('intro')).toBe('Keep');
  });

  it('falls back to Not ad for a category the frontend does not know', () => {
    expect(rejectLabel('something_new')).toBe('Not ad');
  });
});
