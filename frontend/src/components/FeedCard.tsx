import { Link } from 'react-router';
import { Trash2 } from 'lucide-react';
import { Feed } from '../api/types';
import { feedDisplayTitle, feedHasUpstream } from '../utils/feedTitle';
import { formatDate } from '../utils/format';
import Artwork from './Artwork';
import FeedArtworkLink from './FeedArtworkLink';
import { feedArtworkSrc } from '../utils/artworkUrl';
import CopyButton from './CopyButton';
import DropdownMenu from './DropdownMenu';
import FeedStatusSummary from './FeedStatusSummary';
import FeedTypeBadge from './FeedTypeBadge';
import PodpingBadge from './PodpingBadge';
import { btnBase, btnPrimary, btnDestructive } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { cardTitle, cardPad } from './pageStyles';

interface FeedCardProps {
  feed: Feed;
  onRefresh: (slug: string, options?: { force?: boolean }) => void;
  onDelete: (slug: string) => void;
  isRefreshing?: boolean;
}

function FeedCard({ feed, onRefresh, onDelete, isRefreshing }: FeedCardProps) {
  const artworkUrl = feedArtworkSrc(feed.slug, feed.artworkUrl);
  const hasUpstream = feedHasUpstream(feed);

  return (
    <div className="bg-card rounded-lg border border-border h-full flex flex-col">
      <div className="flex flex-1">
        <FeedArtworkLink
          slug={feed.slug}
          className="block w-24 h-24 shrink-0 overflow-hidden rounded-tl-lg cursor-pointer"
        >
          <Artwork
            src={artworkUrl}
            alt={feed.title}
            className="w-full h-full object-cover"
          />
        </FeedArtworkLink>
        <div className={`flex-1 ${cardPad} min-w-0`}>
          <div className="flex items-center gap-2 min-w-0">
            <Link
              to={`/feeds/${feed.slug}`}
              className={`${cardTitle} hover:text-primary truncate block ${focusRing}`}
            >
              {feedDisplayTitle(feed)}
            </Link>
            <FeedTypeBadge feedType={feed.feedType} />
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            {feed.episodeCount} episodes
          </p>
          {/* Every slot below always renders so cards share one height. */}
          <p className="text-xs text-muted-foreground mt-1 truncate">
            {feed.lastRefreshed ? `Updated ${formatDate(feed.lastRefreshed)}` : 'Not refreshed yet'}

          </p>
          {hasUpstream && feed.lastRefreshError && <details className="text-xs text-warning mt-1">
            <summary className="cursor-pointer">Refresh failed</summary>
            <p className="break-words mt-1">{feed.lastRefreshError}</p>
          </details>}
          <div className="h-4 mt-1">
            <PodpingBadge
              coverage={feed.podpingCoverage}
              lastPodpingAt={feed.lastPodpingAt}
              compact
              className="text-xs block truncate"
            />
          </div>
          <div className="mt-2 min-h-[1.375rem]">
            <FeedStatusSummary counts={feed.statusCounts} />
          </div>
        </div>
      </div>
      <div className="px-4 py-3 bg-secondary/50 border-t border-border rounded-b-lg flex justify-between items-center">
        <CopyButton text={feed.feedUrl} hideLabelOnMobile />
        <div className="flex gap-2">
          {hasUpstream && (
            <DropdownMenu
              triggerLabel={isRefreshing ? 'Refreshing...' : 'Refresh'}
              triggerClassName={`${btnBase} ${btnPrimary}`}
              disabled={isRefreshing}
              title="Refresh feed"
              items={[
                {
                  title: 'Refresh',
                  subtitle: 'Check for new episodes',
                  onClick: () => onRefresh(feed.slug),
                },
                {
                  title: 'Force refresh',
                  subtitle: 'Bypass cache',
                  onClick: () => onRefresh(feed.slug, { force: true }),
                },
              ]}
            />
          )}
          <button
            onClick={() => onDelete(feed.slug)}
            className={`${btnBase} ${btnDestructive}`}
            title="Delete feed"
            aria-label="Delete feed"
          >
            <Trash2 className="w-4 h-4 sm:hidden" aria-hidden="true" />
            <span className="hidden sm:inline">Delete</span>
          </button>
        </div>
      </div>
    </div>
  );
}

export default FeedCard;
