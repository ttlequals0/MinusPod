// Segment categories and actions (issue #565): what kind of content a
// detected span covers, and what the pipeline does with it. Mirrors
// config.SEGMENT_CATEGORIES / SEGMENT_ACTIONS -- the single frontend source
// of truth for labels so the global matrix, per-feed overrides, and the
// episode-page chips never drift apart.
export type SegmentCategory =
  | 'sponsor'
  | 'cross_promo'
  | 'self_promo'
  | 'interaction'
  | 'intro'
  | 'outro'
  | 'recap';

export const SEGMENT_CATEGORIES: SegmentCategory[] = [
  'sponsor', 'cross_promo', 'self_promo', 'interaction', 'intro', 'outro', 'recap',
];

export const SEGMENT_CATEGORY_LABELS: Record<SegmentCategory, string> = {
  sponsor: 'Sponsor',
  cross_promo: 'Cross-promo',
  self_promo: 'Self-promo',
  interaction: 'Interaction',
  intro: 'Intro',
  outro: 'Outro',
  recap: 'Recap',
};

export type SegmentAction = 'remove' | 'beep' | 'keep';

export const SEGMENT_ACTIONS: SegmentAction[] = ['remove', 'beep', 'keep'];

export const SEGMENT_ACTION_LABELS: Record<SegmentAction, string> = {
  remove: 'Remove',
  beep: 'Beep',
  keep: 'Keep',
};

export const DEFAULT_SEGMENT_ACTION: SegmentAction = 'remove';
