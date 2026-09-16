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

// Muted marker for a marker whose segment-category action resolved to
// "keep": the audio was left in on purpose, not cut by mistake.
export function KeptBadge() {
  return (
    <span
      className={`${badgeBase} font-medium ${tint.neutral}`}
      title="This segment's category is set to Keep, so it was left in the audio"
    >
      Kept
    </span>
  );
}
