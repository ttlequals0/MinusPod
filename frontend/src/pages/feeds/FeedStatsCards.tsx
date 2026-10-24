import StatTile from '../../components/StatTile';
import { useQuery } from '@tanstack/react-query';
import { getDashboardStats } from '../../api/stats';
import type { Feed } from '../../api/types';
import {
  EPISODE_STATUS_LABELS,
  EPISODE_STATUS_ORDER,
  EPISODE_STATUS_TEXT_COLORS,
} from '../../utils/episodeStatus';
import { formatCost, formatStatsDuration } from '../../utils/format';

interface Props {
  feed: Feed;
  slug: string;
}

// Per-feed health at a glance (#466): a row of status-count cards colored to
// match the episode badges, plus processing totals for this feed.
function FeedStatsCards({ feed, slug }: Props) {
  const { data: stats } = useQuery({
    queryKey: ['stats-dashboard', slug],
    queryFn: () => getDashboardStats(slug),
  });

  const counts = feed.statusCounts;
  // The deferred card only earns its slot while the offline queue holds
  // episodes; the other six render even at zero so the layout stays stable.
  const statusKeys = EPISODE_STATUS_ORDER.filter(
    (key) => key !== 'deferred' || (counts && counts[key] > 0),
  );

  const showAggregates = stats != null && stats.totalEpisodesProcessed > 0;
  if (!counts && !showAggregates) return null;

  return (
    <div className="mb-6 space-y-3">
      {counts && (
        <div className="grid grid-cols-2 sm:grid-cols-6 gap-2 sm:gap-3">
          {statusKeys.map((key) => (
            <StatTile
              key={key}
              value={counts[key]}
              label={<span className="first-letter:uppercase">{EPISODE_STATUS_LABELS[key]}</span>}
              className={EPISODE_STATUS_TEXT_COLORS[key]}
            />
          ))}
        </div>
      )}
      {showAggregates && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3">
          <StatTile value={stats.totalEpisodesProcessed} label="Episodes processed" />
          <StatTile value={stats.totalAdsRemoved} label="Ads removed" />
          <StatTile value={formatStatsDuration(stats.totalTimeSavedSeconds)} label="Time saved" />
          <StatTile value={formatCost(stats.totalLlmCost)} label="Cost" />
        </div>
      )}
    </div>
  );
}

export default FeedStatsCards;
