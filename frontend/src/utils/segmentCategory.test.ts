import { describe, expect, it } from 'vitest';
import type { ShowPromoRules } from '../api/types';
import {
  confirmLabel, isShowPromoCategory, patternSponsorLabel, rejectLabel, showPromoSpanProblem,
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

const RULES: ShowPromoRules = {
  floors: {
    interaction: { seconds: 20, chars: 150 },
    self_promo: { seconds: 20, chars: 150 },
    cross_promo: { seconds: 30, chars: 225 },
  },
  maxSeconds: 120,
};

describe('show patterns', () => {
  it('names the sponsor, else the show for a server-flagged show pattern, else Unknown', () => {
    expect(patternSponsorLabel({ sponsor: 'Acme', is_show_promo: false })).toBe('Acme');
    expect(patternSponsorLabel({ sponsor: null, is_show_promo: true, podcast_name: 'The Diary' }))
      .toBe('The Diary');
    expect(patternSponsorLabel({ sponsor: null, is_show_promo: true })).toBe('This show');
    expect(patternSponsorLabel({ sponsor: null, is_show_promo: false })).toBe('(Unknown)');
    expect(patternSponsorLabel({ sponsor: null })).toBe('(Unknown)');
  });

  it('knows which categories qualify only from the server rules', () => {
    expect(isShowPromoCategory(RULES, 'interaction')).toBe(true);
    expect(isShowPromoCategory(RULES, 'sponsor')).toBe(false);
    expect(isShowPromoCategory(RULES, null)).toBe(false);
    expect(isShowPromoCategory(undefined, 'interaction')).toBe(false);
    const custom: ShowPromoRules = { floors: { recap: { seconds: 5, chars: 10 } }, maxSeconds: 60 };
    expect(isShowPromoCategory(custom, 'recap')).toBe(true);
  });

  it('judges a span with the rules it was given', () => {
    expect(showPromoSpanProblem(RULES, 'interaction', 20, 150)).toBeNull();
    expect(showPromoSpanProblem(RULES, 'self_promo', 19, 200)).toMatch(/at least 20 seconds/);
    expect(showPromoSpanProblem(RULES, 'interaction', 30, 149)).toMatch(/at least 150 characters/);
    expect(showPromoSpanProblem(RULES, 'cross_promo', 29, 300)).toMatch(/at least 30 seconds/);
    expect(showPromoSpanProblem(RULES, 'cross_promo', 31, 224)).toMatch(/at least 225 characters/);
    expect(showPromoSpanProblem(RULES, 'interaction', 300, 200)).toMatch(/at most 120 seconds/);
    expect(showPromoSpanProblem(RULES, 'interaction', 25)).toBeNull();      // characters unknown
    expect(showPromoSpanProblem(RULES, 'sponsor', 1, 1)).toBeNull();
    // a raised minimum from the server is honoured; no rules means no pre-check
    const raised = { ...RULES, floors: { interaction: { seconds: 40, chars: 150 } } };
    expect(showPromoSpanProblem(raised, 'interaction', 30, 200)).toMatch(/at least 40 seconds/);
    expect(showPromoSpanProblem(undefined, 'interaction', 1, 1)).toBeNull();
  });

  it('hints that confirming teaches the show only when it really will', () => {
    const base = { category: 'interaction', sponsor: undefined, patternId: null, start: 100, end: 130 };
    expect(teachesShowHint(RULES, base)).toMatch(/teaches this show/i);
    expect(teachesShowHint(RULES, { ...base, category: 'cross_promo', end: 140 })).toMatch(/teaches this show/i);
    expect(teachesShowHint(RULES, { ...base, sponsor: 'Patreon' })).toBeNull();
    expect(teachesShowHint(RULES, { ...base, patternId: 4 })).toBeNull();
    expect(teachesShowHint(RULES, { ...base, end: 110 })).toBeNull();
    expect(teachesShowHint(RULES, { ...base, end: 400 })).toBeNull();
    expect(teachesShowHint(RULES, { ...base, category: 'cross_promo', end: 129 })).toBeNull();
    expect(teachesShowHint(RULES, { ...base, category: 'sponsor' })).toBeNull();
    expect(teachesShowHint(RULES, { ...base, category: undefined })).toBeNull();
    expect(teachesShowHint(undefined, base)).toBeNull();
  });
});
