import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { ListMusic } from 'lucide-react';
import { Feed, Episode, EpisodeSummary } from '../api/types';
import { feedDisplayTitle } from '../utils/feedTitle';
import { feedArtworkSrc } from '../utils/artworkUrl';
import { isFailedStatus } from '../utils/episodeStatus';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import Artwork from './Artwork';
import ChevronCaret from './ChevronCaret';
import FeedTypeBadge from './FeedTypeBadge';
import { EpisodeRow } from './EpisodeList';
import EpisodeRowActions from './EpisodeRowActions';
import { btnBase, btnSecondary } from './buttonStyles';
import { badgeBase, tint } from './badgeStyles';
import { focusRing } from './fieldStyles';
import { cardTitle, cardPad } from './pageStyles';

export const MIN_EPISODES_PER_PODCAST = 1;
export const MAX_EPISODES_PER_PODCAST = 10;
export const DEFAULT_EPISODES_PER_PODCAST = 3;

export function clampEpisodesPerPodcast(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_EPISODES_PER_PODCAST;
  return Math.min(MAX_EPISODES_PER_PODCAST, Math.max(MIN_EPISODES_PER_PODCAST, Math.round(value)));
}

// EpisodeSummary carries every field EpisodeRow renders except feedSlug and
// ad_count, which the projection does not include.
function toEpisode(summary: EpisodeSummary): Episode {
  return {
    id: summary.id,
    title: summary.title,
    published: summary.published,
    processedAt: summary.processedAt,
    duration: summary.duration,
    status: summary.status,
    jobState: summary.jobState,
    artworkUrl: summary.artworkUrl ?? undefined,
    artworkThumbUrl: summary.artworkThumbUrl,
    description: summary.description ?? undefined,
    error: summary.error,
    pendingReviewCount: summary.pendingReviewCount,
    passthroughEnabled: summary.passthroughEnabled,
    hasBeenProcessed: summary.hasBeenProcessed,
  };
}

interface DashboardEpisodeGroupsProps {
  feeds: Feed[];
  episodesPerPodcast: number;
}

interface BulkCollapseSignal {
  seq: number;
  collapsed: boolean;
}

// One section per podcast with its latest episodes. Recents is excluded: its
// episodes belong to other podcasts' podcast_id, so latestEpisodes is always
// empty for it and it would otherwise render as a broken, empty card.
function DashboardEpisodeGroups({ feeds, episodesPerPodcast }: DashboardEpisodeGroupsProps) {
  const groupedFeeds = feeds.filter((feed) => feed.feedType !== 'recents');
  const [bulkSignal, setBulkSignal] = useState<BulkCollapseSignal | null>(null);

  if (groupedFeeds.length === 0) {
    return (
      <div className="text-center py-12 bg-card rounded-lg border border-border">
        <p className="text-muted-foreground">No feeds to show episodes for yet.</p>
      </div>
    );
  }

  const bulkBtnClass = `${btnBase} ${btnSecondary} text-xs px-2.5 py-1`;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => setBulkSignal((prev) => ({ seq: (prev?.seq ?? 0) + 1, collapsed: false }))}
          className={bulkBtnClass}
        >
          Expand all
        </button>
        <button
          type="button"
          onClick={() => setBulkSignal((prev) => ({ seq: (prev?.seq ?? 0) + 1, collapsed: true }))}
          className={bulkBtnClass}
        >
          Collapse all
        </button>
      </div>
      {groupedFeeds.map((feed) => (
        <FeedEpisodeGroup key={feed.slug} feed={feed} limit={episodesPerPodcast} bulkSignal={bulkSignal} />
      ))}
    </div>
  );
}

function FeedEpisodeGroup({
  feed,
  limit,
  bulkSignal,
}: {
  feed: Feed;
  limit: number;
  bulkSignal: BulkCollapseSignal | null;
}) {
  const episodes = (feed.latestEpisodes ?? []).slice(0, limit);
  const artworkUrl = feedArtworkSrc(feed.slug, feed.artworkUrl);
  const headingId = `episode-group-heading-${feed.slug}`;
  const [collapsed, setCollapsed] = useLocalStorageState<boolean>(
    `dashboard-group-collapsed:${feed.slug}`, false);
  const lastBulkSeq = useRef(bulkSignal?.seq);

  useEffect(() => {
    if (!bulkSignal || bulkSignal.seq === lastBulkSeq.current) return;
    lastBulkSeq.current = bulkSignal.seq;
    setCollapsed(bulkSignal.collapsed);
  }, [bulkSignal, setCollapsed]);

  const heldCount = episodes.reduce((sum, ep) => sum + (ep.pendingReviewCount ?? 0), 0);
  const failedCount = episodes.filter((ep) => isFailedStatus(ep.status)).length;
  const activeCount = episodes.filter(
    (ep) => ep.status === 'processing' || ep.jobState === 'processing' || ep.jobState === 'queued',
  ).length;

  return (
    <section aria-labelledby={headingId} className="bg-card rounded-lg border border-border">
      <div className={`flex items-center gap-3 ${cardPad}${collapsed ? '' : ' border-b border-border'}`}>
        <Link
          to={`/feeds/${feed.slug}`}
          aria-label={`${feedDisplayTitle(feed)} cover art`}
          className={`block w-12 h-12 shrink-0 overflow-hidden rounded-md ${focusRing}`}
        >
          <Artwork
            src={artworkUrl}
            alt=""
            loading="lazy"
            className="w-full h-full object-cover"
          />
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 min-w-0">
            <h2 id={headingId} className={`${cardTitle} truncate`}>
              <Link to={`/feeds/${feed.slug}`} className={`hover:text-primary ${focusRing}`}>
                {feedDisplayTitle(feed)}
              </Link>
            </h2>
            <FeedTypeBadge feedType={feed.feedType} />
            {collapsed && heldCount > 0 && (
              <span className={`${badgeBase} ${tint.warning} shrink-0`}>
                {heldCount} held
              </span>
            )}
            {collapsed && failedCount > 0 && (
              <span className={`${badgeBase} ${tint.destructive} shrink-0`}>
                {failedCount} failed
              </span>
            )}
            {collapsed && activeCount > 0 && (
              <span className={`${badgeBase} ${tint.blue} shrink-0`}>
                {activeCount} active
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{feed.episodeCount} episodes</p>
        </div>
        <Link
          to={`/feeds/${feed.slug}`}
          aria-label={`View all episodes of ${feedDisplayTitle(feed)}`}
          title="View all episodes"
          className={`shrink-0 inline-flex items-center justify-center rounded-md text-sm text-primary p-3 hover:bg-muted sm:p-0 sm:hover:bg-transparent sm:hover:underline ${focusRing}`}
        >
          <ListMusic aria-hidden="true" className="w-5 h-5 sm:hidden" />
          <span className="hidden sm:inline">View all episodes</span>
        </Link>
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          aria-expanded={!collapsed}
          aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${feedDisplayTitle(feed)}`}
          className={`shrink-0 p-3 rounded-md hover:bg-muted ${focusRing}`}
        >
          <ChevronCaret expanded={!collapsed} className="w-5 h-5" />
        </button>
      </div>
      {!collapsed && (
        <div>
        {episodes.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">No episodes yet.</p>
        ) : (
          <div className="p-3 flex flex-col gap-2">
            {episodes.map((summary) => (
              <EpisodeRow
                key={summary.id}
                episode={toEpisode(summary)}
                feedSlug={feed.slug}
                feedArtworkUrl={feed.artworkUrl}
                selected={false}
                renderActions={() => (
                  <EpisodeRowActions
                    feedSlug={feed.slug}
                    episodeId={summary.id}
                    status={summary.status}
                    jobState={summary.jobState}
                    hasBeenProcessed={summary.hasBeenProcessed}
                    feedType={feed.feedType}
                  />
                )}
              />
            ))}
          </div>
        )}
        </div>
      )}
    </section>
  );
}

export default DashboardEpisodeGroups;
