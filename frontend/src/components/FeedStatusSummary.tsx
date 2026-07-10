import type { EpisodeStatusCounts } from '../api/types';
import {
  EPISODE_STATUS_COLORS,
  EPISODE_STATUS_LABELS,
  EPISODE_STATUS_ORDER,
  EPISODE_STATUS_SHORT_LABELS,
} from '../utils/episodeStatus';

interface Props {
  counts?: EpisodeStatusCounts;
  className?: string;
}

// Compact per-feed status pills for the dashboard (#466). Reuses the exact
// episode badge palette so the colors read the same as inside the feed.
function FeedStatusSummary({ counts, className = '' }: Props) {
  if (!counts) return null;
  const visible = EPISODE_STATUS_ORDER.filter((key) => counts[key] > 0);
  if (visible.length === 0) return null;

  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {visible.map((key) => (
        <span
          key={key}
          className={`px-1.5 py-0.5 rounded text-xs font-medium whitespace-nowrap ${EPISODE_STATUS_COLORS[key]}`}
          title={`${counts[key]} ${EPISODE_STATUS_LABELS[key]}`}
        >
          {counts[key]} {EPISODE_STATUS_SHORT_LABELS[key]}
        </span>
      ))}
    </div>
  );
}

export default FeedStatusSummary;
