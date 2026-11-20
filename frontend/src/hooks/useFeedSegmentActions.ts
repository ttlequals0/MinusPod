import { useQuery } from '@tanstack/react-query';
import { getFeed } from '../api/feeds';
import { getSettings } from '../api/settings';
import {
  DEFAULT_SEGMENT_ACTION, SEGMENT_CATEGORIES,
  type SegmentAction, type SegmentCategory,
} from '../utils/segmentCategory';

export type SegmentActionMap = Record<SegmentCategory, SegmentAction>;

// Mirrors resolve_segment_actions in src/database/podcasts.py: the feed's
// override, then the global setting, then the default.
export function resolveSegmentActions(
  feedOverrides: Partial<Record<SegmentCategory, SegmentAction>> | null | undefined,
  globalActions: Partial<Record<SegmentCategory, SegmentAction>> | null | undefined,
): SegmentActionMap {
  const resolved = {} as SegmentActionMap;
  for (const category of SEGMENT_CATEGORIES) {
    resolved[category] = feedOverrides?.[category] ?? globalActions?.[category] ?? DEFAULT_SEGMENT_ACTION;
  }
  return resolved;
}

// The action each category resolves to for one feed right now. Null until the
// feed and the settings have both loaded, so a caller never acts on half a map.
export function useFeedSegmentActions(slug?: string | null): SegmentActionMap | null {
  const settings = useQuery({ queryKey: ['settings'], queryFn: getSettings, enabled: !!slug });
  const feed = useQuery({ queryKey: ['feed', slug], queryFn: () => getFeed(slug!), enabled: !!slug });
  if (!slug || !settings.data || !feed.data) return null;
  return resolveSegmentActions(feed.data.segmentCategoryActions, settings.data.segmentCategoryActions?.value);
}
