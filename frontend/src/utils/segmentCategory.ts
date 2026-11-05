import type { ShowPromoRules } from '../api/types';

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

// Show patterns (an interaction, self-promo or cross-promo read learned for one
// show). The server decides what qualifies and sends its rules with the
// settings (ShowPromoRules); nothing here repeats a floor or a category list.

// Whether a read in this category can be learned for a show.
export function isShowPromoCategory(
  rules: ShowPromoRules | undefined, category?: string | null,
): boolean {
  return !!rules && !!category && category in rules.floors;
}

// What the Patterns page shows where a sponsor name goes: a show pattern has no
// sponsor by design, so it names the show instead of reading as a defect.
export function patternSponsorLabel(pattern: {
  sponsor?: string | null; is_show_promo?: boolean; podcast_name?: string | null;
}): string {
  if (pattern.sponsor) {
    return pattern.sponsor;
  }
  return pattern.is_show_promo ? (pattern.podcast_name || 'This show') : '(Unknown)';
}

// Why a span cannot become a show pattern, in plain words, or null (also null
// while the rules are unknown: the server still judges). Characters are
// optional because an episode row does not carry the transcript text.
export function showPromoSpanProblem(
  rules: ShowPromoRules | undefined, category: string | null | undefined,
  seconds: number, chars?: number,
): string | null {
  if (!rules || !category || !isShowPromoCategory(rules, category)) {
    return null;
  }
  const label = SEGMENT_CATEGORY_LABELS[category as SegmentCategory]?.toLowerCase() ?? category;
  const floor = rules.floors[category];
  if (seconds < floor.seconds) {
    return `A ${label} read must run at least ${floor.seconds} seconds to be learned for this show.`;
  }
  if (seconds > rules.maxSeconds) {
    return `A ${label} read can run at most ${rules.maxSeconds} seconds to be learned for this show.`;
  }
  if (chars !== undefined && chars < floor.chars) {
    return `A ${label} read needs at least ${floor.chars} characters of text to be learned for this show.`;
  }
  return null;
}

// One line under a Confirm button, shown only when confirming will really
// create a show pattern: a promo category, no sponsor named, no pattern
// already linked and a span within the server's limits.
export function teachesShowHint(rules: ShowPromoRules | undefined, marker: {
  category?: string | null; sponsor?: string | null; patternId?: number | null;
  start: number; end: number;
}): string | null {
  if (!isShowPromoCategory(rules, marker.category) || marker.sponsor || marker.patternId != null) {
    return null;
  }
  if (showPromoSpanProblem(rules, marker.category, marker.end - marker.start) !== null) {
    return null;
  }
  return 'Confirming teaches this show to cut this read automatically in later episodes.';
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
