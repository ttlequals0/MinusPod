// Segment categories and actions (issue #565). Mirrors
// config.SEGMENT_CATEGORIES / SEGMENT_ACTIONS as the single frontend source
// of truth for labels, so the matrix, overrides, and episode chips agree.
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

export const SEGMENT_CATEGORY_DESCRIPTIONS: Record<SegmentCategory, string> = {
  sponsor: 'Paid ads, including dynamically inserted ones',
  cross_promo: 'Promos for other shows and the network',
  self_promo: "The show's own Patreon, merch, and subscribe asks",
  interaction: 'Follow, rate, and review reminders',
  intro: 'Opening theme and welcome',
  outro: 'Closing credits and sign-off',
  recap: 'Previews and coming-up bumpers',
};

export type SegmentAction = 'remove' | 'beep' | 'keep';

export const SEGMENT_ACTIONS: SegmentAction[] = ['remove', 'beep', 'keep'];

export const SEGMENT_ACTION_LABELS: Record<SegmentAction, string> = {
  remove: 'Remove',
  beep: 'Beep',
  keep: 'Keep',
};

export const DEFAULT_SEGMENT_ACTION: SegmentAction = 'remove';

// Filter value for markers no detection stage classified. Not a member of
// SegmentCategory: unset is the absence of a category, not a category. Matches
// detection_review.UNSET_CATEGORY on the backend.
export const UNSET_CATEGORY = 'none';

export type CategoryFilter = SegmentCategory | typeof UNSET_CATEGORY | '';

// Options for the category selects on the patterns, ad review, and detected ads
// views. Derived from the label map so a new category needs one edit, not four.
export const SEGMENT_CATEGORY_FILTER_OPTIONS: Array<[CategoryFilter, string]> = [
  ['', 'All categories'],
  ...SEGMENT_CATEGORIES.map(
    (c) => [c, SEGMENT_CATEGORY_LABELS[c]] as [CategoryFilter, string],
  ),
  [UNSET_CATEGORY, 'Uncategorized'],
];
