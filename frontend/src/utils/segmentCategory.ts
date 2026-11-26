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

// Sponsor, uncategorized and unknown categories all count as an ad.
function isAdCategory(category?: string | null): boolean {
  return !category || category === 'sponsor' || !(category in SEGMENT_CATEGORY_LABELS);
}

// Label for the button that approves a held or rejected marker. A sponsor or
// uncategorized marker is an ad; any other category names itself, so confirming
// a subscribe reminder doesn't read as calling it an ad.
export function confirmLabel(category?: string | null): string {
  if (isAdCategory(category)) {
    return 'Confirm';
  }
  return `Confirm ${SEGMENT_CATEGORY_LABELS[category as SegmentCategory].toLowerCase()}`;
}

// Label for the button that rejects a marker. Only a sponsor (or uncategorized)
// marker is an ad; any other known category is kept rather than called "not an ad".
export function rejectLabel(category?: string | null): string {
  if (isAdCategory(category)) {
    return 'Not ad';
  }
  return 'Keep';
}

export type SegmentAction = 'remove' | 'beep' | 'keep' | 'mark';

export const SEGMENT_ACTIONS: SegmentAction[] = ['remove', 'beep', 'keep', 'mark'];

export const SEGMENT_ACTION_LABELS: Record<SegmentAction, string> = {
  remove: 'Remove',
  beep: 'Beep',
  keep: 'Keep',
  mark: 'Mark',
};

export const DEFAULT_SEGMENT_ACTION: SegmentAction = 'remove';

// Mirrors is_keep_like in src/config.py: mark behaves like keep everywhere
// audio is concerned, differing only in whether it gets a chapter.
export function isKeepLike(action?: string | null): boolean {
  return action === 'keep' || action === 'mark';
}

// The action a marker's category resolves to. Mirrors normalize_segment_category
// in src/config.py: an unset or unknown category resolves as sponsor. Null when
// the feed's actions are not known yet.
export function resolveCategoryAction(
  category: string | null | undefined,
  actions: Partial<Record<SegmentCategory, SegmentAction>> | null | undefined,
): SegmentAction | null {
  if (!actions) return null;
  const known = category && category in SEGMENT_CATEGORY_LABELS ? (category as SegmentCategory) : 'sponsor';
  return actions[known] ?? null;
}

// True when the category's current action for the feed leaves the marker in
// the audio, so a confirm or reject verdict would not change it: a recut sorts
// markers by their category's action before it reads any verdict. A pattern
// or a differential cuts a marker whatever its category says and stamps it
// remove or beep, so that stamp wins until the category is changed.
export function isKeptByCategory(
  category: string | null | undefined,
  actionApplied: string | null | undefined,
  actions: Partial<Record<SegmentCategory, SegmentAction>> | null | undefined,
  categoryUnchanged = true,
): boolean {
  const stampedCut = actionApplied === 'remove' || actionApplied === 'beep';
  return isKeepLike(resolveCategoryAction(category, actions)) && !(stampedCut && categoryUnchanged);
}

// Filter value for markers no detection stage classified. Not a member of
// SegmentCategory: unset is the absence of a category, not a category. Matches
// detection_review.UNSET_CATEGORY on the backend.
export const UNSET_CATEGORY = 'none';

type CategoryFilter = SegmentCategory | typeof UNSET_CATEGORY | '';

// Options for the category selects on the patterns, ad review, and detected ads
// views. Derived from the label map so a new category needs one edit, not four.
export const SEGMENT_CATEGORY_FILTER_OPTIONS: Array<[CategoryFilter, string]> = [
  ['', 'All categories'],
  ...SEGMENT_CATEGORIES.map(
    (c) => [c, SEGMENT_CATEGORY_LABELS[c]] as [CategoryFilter, string],
  ),
  [UNSET_CATEGORY, 'Uncategorized'],
];
