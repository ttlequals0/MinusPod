import { Link } from 'react-router-dom';
import { Trash2 } from 'lucide-react';
import { Feed } from '../api/types';
import { feedDisplayTitle } from '../utils/feedTitle';
import Artwork from './Artwork';
import CopyButton from './CopyButton';
import DropdownMenu from './DropdownMenu';

interface FeedCardProps {
  feed: Feed;
  onRefresh: (slug: string, options?: { force?: boolean }) => void;
  onDelete: (slug: string) => void;
  isRefreshing?: boolean;
}

function FeedCard({ feed, onRefresh, onDelete, isRefreshing }: FeedCardProps) {
  const artworkUrl = feed.artworkUrl || `/api/v1/feeds/${feed.slug}/artwork`;

  return (
    <div className="bg-card rounded-lg border border-border">
      <div className="flex">
        <div className="w-24 h-24 shrink-0 overflow-hidden rounded-tl-lg">
          <Artwork
            src={artworkUrl}
            alt={feed.title}
            className="w-full h-full object-cover"
          />
        </div>
        <div className="flex-1 p-4 min-w-0">
          <Link
            to={`/feeds/${feed.slug}`}
            className="text-lg font-semibold text-foreground hover:text-primary truncate block"
          >
            {feedDisplayTitle(feed)}
          </Link>
          <p className="text-sm text-muted-foreground mt-1">
            {feed.episodeCount} episodes
          </p>
          {feed.lastRefreshed && (
            <p className="text-xs text-muted-foreground mt-1">
              Updated {new Date(feed.lastRefreshed).toLocaleDateString()}
            </p>
          )}
        </div>
      </div>
      <div className="px-4 py-3 bg-secondary/50 border-t border-border rounded-b-lg flex justify-between items-center">
        <CopyButton text={feed.feedUrl} hideLabelOnMobile />
        <div className="flex gap-2">
          <DropdownMenu
            triggerLabel={isRefreshing ? 'Refreshing...' : 'Refresh'}
            triggerClassName="px-3 py-1.5 sm:px-4 sm:py-2 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors flex items-center gap-2 whitespace-nowrap"
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
          <button
            onClick={() => onDelete(feed.slug)}
            className="inline-flex items-center justify-center gap-2 px-3 py-1.5 sm:px-4 sm:py-2 text-sm rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors"
            title="Delete feed"
            aria-label="Delete feed"
          >
            <Trash2 className="w-4 h-4 sm:hidden" />
            <span className="hidden sm:inline">Delete</span>
          </button>
        </div>
      </div>
    </div>
  );
}

export default FeedCard;
