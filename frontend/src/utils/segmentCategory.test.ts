import { describe, expect, it } from 'vitest';
import {
  confirmLabel, isShowPromoPattern, patternSponsorLabel, rejectLabel, showPromoSpanProblem,
  teachesShowHint,
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

const promoRow = {
  created_by: 'show_promo', scope: 'podcast', sponsor: null, category: 'interaction',
};

describe('show promo patterns', () => {
  it('needs the explicit provenance, podcast scope, no sponsor and a promo category', () => {
    expect(isShowPromoPattern(promoRow)).toBe(true);
    expect(isShowPromoPattern({ ...promoRow, sponsor: '' })).toBe(true);
    expect(isShowPromoPattern({ ...promoRow, category: 'cross_promo' })).toBe(true);
    // legacy sponsorless rows, hand-made rows and rows whose sponsor was deleted
    expect(isShowPromoPattern({ ...promoRow, created_by: 'auto' })).toBe(false);
    expect(isShowPromoPattern({ ...promoRow, created_by: 'user' })).toBe(false);
    expect(isShowPromoPattern({ ...promoRow, created_by: null })).toBe(false);
    expect(isShowPromoPattern({ sponsor: null, category: 'interaction' })).toBe(false);
    // global rows, rows with a sponsor, other categories
    expect(isShowPromoPattern({ ...promoRow, scope: 'global' })).toBe(false);
    expect(isShowPromoPattern({ ...promoRow, sponsor: 'Acme' })).toBe(false);
    expect(isShowPromoPattern({ ...promoRow, category: 'sponsor' })).toBe(false);
    expect(isShowPromoPattern({ ...promoRow, category: 'intro' })).toBe(false);
  });

  it('names the sponsor, else the show for a show promo, else Unknown', () => {
    expect(patternSponsorLabel({ ...promoRow, sponsor: 'Acme', category: 'sponsor' })).toBe('Acme');
    expect(patternSponsorLabel({ ...promoRow, podcast_name: 'The Diary' })).toBe('The Diary');
    expect(patternSponsorLabel(promoRow)).toBe('This show');
    expect(patternSponsorLabel({ ...promoRow, created_by: 'auto' })).toBe('(Unknown)');
  });

  it('mirrors the server floors', () => {
    expect(showPromoSpanProblem('interaction', 20, 150)).toBeNull();
    expect(showPromoSpanProblem('self_promo', 19, 200)).toMatch(/at least 20 seconds/);
    expect(showPromoSpanProblem('interaction', 30, 149)).toMatch(/at least 150 characters/);
    expect(showPromoSpanProblem('cross_promo', 29, 300)).toMatch(/at least 30 seconds/);
    expect(showPromoSpanProblem('cross_promo', 31, 224)).toMatch(/at least 225 characters/);
    expect(showPromoSpanProblem('interaction', 300, 200)).toMatch(/at most 120 seconds/);
    // characters unknown: only the duration is judged
    expect(showPromoSpanProblem('interaction', 25)).toBeNull();
    expect(showPromoSpanProblem('sponsor', 1, 1)).toBeNull();
  });

  it('hints that confirming teaches the show only when it really will', () => {
    const base = { category: 'interaction', sponsor: undefined, patternId: null, start: 100, end: 130 };
    expect(teachesShowHint(base)).toMatch(/teaches this show/i);
    expect(teachesShowHint({ ...base, category: 'cross_promo', end: 140 })).toMatch(/teaches this show/i);
    expect(teachesShowHint({ ...base, sponsor: 'Patreon' })).toBeNull();      // a named sponsor
    expect(teachesShowHint({ ...base, patternId: 4 })).toBeNull();            // already linked
    expect(teachesShowHint({ ...base, end: 110 })).toBeNull();                // under the floor
    expect(teachesShowHint({ ...base, end: 400 })).toBeNull();                // over the ceiling
    expect(teachesShowHint({ ...base, category: 'cross_promo', end: 129 })).toBeNull();
    expect(teachesShowHint({ ...base, category: 'sponsor' })).toBeNull();
    expect(teachesShowHint({ ...base, category: undefined })).toBeNull();
  });
});
