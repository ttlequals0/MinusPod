import { Link } from 'react-router';

import { Feed } from '../api/types';
import { feedDisplayTitle, feedHasUpstream } from '../utils/feedTitle';
import { formatDate } from '../utils/format';
import Artwork from './Artwork';
import FeedArtworkLink from './FeedArtworkLink';
import { feedArtworkSrc } from '../utils/artworkUrl';
import CopyButton from './CopyButton';
import FeedStatusSummary from './FeedStatusSummary';
import FeedTypeBadge from './FeedTypeBadge';
import PodpingBadge from './PodpingBadge';
import { FeedRefreshMenu, FeedDeleteButton } from './FeedRowActions';
import { focusRing } from './fieldStyles';

interface FeedListItemProps {
  feed: Feed;
  onRefresh: (slug: string, options?: { force?: boolean }) => void;
  onDelete: (slug: string) => void;
  isRefreshing?: boolean;
}

function FeedListItem({ feed, onRefresh, onDelete, isRefreshing }: FeedListItemProps) {
  const artworkUrl = feedArtworkSrc(feed.slug, feed.artworkUrl);
  const hasUpstream = feedHasUpstream(feed);

  return (
    <div className="bg-card rounded-lg border border-border p-3 flex items-center gap-3 sm:gap-4">
      <FeedArtworkLink slug={feed.slug} className="block w-10 h-10 shrink-0 cursor-pointer">
        <Artwork
          src={artworkUrl}
          alt={feed.title}
          className="w-full h-full object-cover rounded"
        />
      </FeedArtworkLink>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <Link
            to={`/feeds/${feed.slug}`}
            className={`text-sm font-semibold text-foreground hover:text-primary truncate block ${focusRing}`}
          >
            {feedDisplayTitle(feed)}
          </Link>
          <FeedTypeBadge feedType={feed.feedType} />
        </div>
        <p className="text-xs text-muted-foreground truncate">
          {feed.episodeCount} episodes
          {(feed.awaitingApprovalCount ?? 0) > 0 && (
            <span className="ml-2 text-warning">{feed.awaitingApprovalCount} awaiting approval</span>
          )}
          {feed.lastRefreshed && (
            <span className="ml-2">
              Updated {formatDate(feed.lastRefreshed)}
            </span>
          )}
          <PodpingBadge
            coverage={feed.podpingCoverage}
            lastPodpingAt={feed.lastPodpingAt}
            compact
            className="ml-2"
          />
          {hasUpstream && feed.lastRefreshError && (
            <span
              className="ml-2 text-warning"
              title={feed.lastRefreshError}
            >
              Refresh failing
            </span>
          )}
        </p>
        <FeedStatusSummary counts={feed.statusCounts} className="mt-1" />
      </div>
      <div className="flex items-center gap-1 sm:gap-2 shrink-0">
        <CopyButton text={feed.feedUrl} hideLabelOnMobile />
        {hasUpstream && (
          <>
            <div>
              <FeedRefreshMenu slug={feed.slug} onRefresh={onRefresh} isRefreshing={isRefreshing} compact />
            </div>
          </>
        )}
        <FeedDeleteButton slug={feed.slug} onDelete={onDelete} iconClassName="w-4 h-4" />
      </div>
    </div>
  );
}

export default FeedListItem;
