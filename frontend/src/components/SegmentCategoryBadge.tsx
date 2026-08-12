import { SEGMENT_CATEGORY_LABELS, type SegmentCategory } from '../utils/segmentCategory';

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
        className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground"
        title="No detection stage classified this segment"
      >
        Uncategorized
      </span>
    );
  }
  const label = SEGMENT_CATEGORY_LABELS[category as SegmentCategory] ?? category;
  return (
    <span className="px-1.5 py-0.5 text-xs rounded font-medium bg-c-purple/20 text-c-purple">
      {label}
    </span>
  );
}

// Muted marker for a marker whose segment-category action resolved to
// "keep": the audio was left in on purpose, not cut by mistake.
export function KeptBadge() {
  return (
    <span
      className="px-1.5 py-0.5 text-xs rounded font-medium bg-muted text-muted-foreground"
      title="This segment's category is set to Keep, so it was left in the audio"
    >
      Kept
    </span>
  );
}
