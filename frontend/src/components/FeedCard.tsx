import { Link } from 'react-router-dom';
import { Feed } from '../api/types';
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
    <div className="bg-card rounded-lg border border-border overflow-hidden">
      <div className="flex">
        <div className="w-24 h-24 flex-shrink-0">
          <img
            src={artworkUrl}
            alt={feed.title}
            className="w-full h-full object-cover"
            onError={(e) => {
              (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="%239ca3af"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>';
            }}
          />
        </div>
        <div className="flex-1 p-4 min-w-0">
          <Link
            to={`/feeds/${feed.slug}`}
            className="text-lg font-semibold text-foreground hover:text-primary truncate block"
          >
            {feed.title}
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
      <div className="px-4 py-3 bg-secondary/50 border-t border-border flex justify-between items-center">
        <CopyButton text={feed.feedUrl} />
        <div className="flex gap-2">
          <DropdownMenu
            triggerLabel={isRefreshing ? 'Refreshing...' : 'Refresh'}
            triggerClassName="px-3 py-1 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors flex items-center gap-1.5"
            disabled={isRefreshing}
            title="Refresh feed"
            chevronClassName="w-3 h-3"
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
            className="px-3 py-1 text-sm rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

export default FeedCard;
