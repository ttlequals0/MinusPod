import { SEGMENT_CATEGORY_LABELS, type SegmentCategory } from '../utils/segmentCategory';
import { badgeBase, tint } from './badgeStyles';

// Category pill shared by every marker listing on the episode page. Mirrors
// StageBadge's shape: unknown categories fall back to the raw value with
// neutral styling so a new backend category never renders as an empty badge.
export function SegmentCategoryBadge({ category }: { category?: string | null }) {
  // No category means no stage classified this marker, which is not the same
  // as a sponsor read. Say so rather than rendering nothing, so the gap is
  // visible instead of looking like a missing badge.
  if (!category) {
    return (
      <span
        className={`${badgeBase} font-medium ${tint.neutral}`}
        title="No detection stage classified this segment"
      >
        Uncategorized
      </span>
    );
  }
  const label = SEGMENT_CATEGORY_LABELS[category as SegmentCategory] ?? category;
  return (
    <span className={`${badgeBase} font-medium ${tint.purple}`}>
      {label}
    </span>
  );
}

// Muted marker for a "keep" or "mark" marker: left in on purpose, not cut by
// mistake; mark also publishes a skippable chapter. Takes the raw
// actionApplied and derives the label, rather than asking callers to narrow it.
export function KeptBadge({ actionApplied }: { actionApplied?: string | null }) {
  const marked = actionApplied === 'mark';
  return (
    <span
      className={`${badgeBase} font-medium ${tint.neutral}`}
      title={marked
        ? "This segment's category is set to Mark, so it was left in the audio and published as a chapter"
        : "This segment's category is set to Keep, so it was left in the audio"}
    >
      {marked ? 'Marked' : 'Kept'}
    </span>
  );
}
