import { describe, expect, it } from 'vitest';
import {
  confirmLabel, isShowPromoPattern, patternSponsorLabel, rejectLabel, teachesShowHint,
} from './segmentCategory';

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

describe('show promo patterns', () => {
  it('recognises a sponsorless pattern in a non-sponsor ad category', () => {
    expect(isShowPromoPattern({ sponsor: null, category: 'interaction' })).toBe(true);
    expect(isShowPromoPattern({ sponsor: '', category: 'self_promo' })).toBe(true);
    expect(isShowPromoPattern({ sponsor: null, category: 'cross_promo' })).toBe(true);
    expect(isShowPromoPattern({ sponsor: 'Acme', category: 'cross_promo' })).toBe(false);
    expect(isShowPromoPattern({ sponsor: null, category: 'sponsor' })).toBe(false);
    expect(isShowPromoPattern({ sponsor: null, category: 'intro' })).toBe(false);
    expect(isShowPromoPattern({ sponsor: null })).toBe(false);
  });

  it('names the sponsor, else the show, else Unknown', () => {
    expect(patternSponsorLabel({ sponsor: 'Acme', category: 'sponsor' })).toBe('Acme');
    expect(patternSponsorLabel({ sponsor: null, category: 'interaction', podcast_name: 'The Diary' }))
      .toBe('The Diary');
    expect(patternSponsorLabel({ sponsor: null, category: 'interaction' })).toBe('This show');
    expect(patternSponsorLabel({ sponsor: null, category: 'sponsor' })).toBe('(Unknown)');
  });

  it('says confirming teaches the show only for those categories', () => {
    expect(teachesShowHint('interaction')).toMatch(/this show/i);
    expect(teachesShowHint('cross_promo')).toMatch(/this show/i);
    expect(teachesShowHint('sponsor')).toBeNull();
    expect(teachesShowHint('intro')).toBeNull();
    expect(teachesShowHint(undefined)).toBeNull();
  });
});
