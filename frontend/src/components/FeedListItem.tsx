import { Link } from 'react-router-dom';
import { RefreshCw, Trash2 } from 'lucide-react';

import { Feed } from '../api/types';
import Artwork from './Artwork';
import CopyButton from './CopyButton';
import DropdownMenu from './DropdownMenu';

interface FeedListItemProps {
  feed: Feed;
  onRefresh: (slug: string, options?: { force?: boolean }) => void;
  onDelete: (slug: string) => void;
  isRefreshing?: boolean;
}

function FeedListItem({ feed, onRefresh, onDelete, isRefreshing }: FeedListItemProps) {
  const artworkUrl = feed.artworkUrl || `/api/v1/feeds/${feed.slug}/artwork`;

  return (
    <div className="bg-card rounded-lg border border-border p-3 flex items-center gap-3 sm:gap-4">
      <div className="w-10 h-10 shrink-0">
        <Artwork
          src={artworkUrl}
          alt={feed.title}
          className="w-full h-full object-cover rounded"
        />
      </div>
      <div className="flex-1 min-w-0">
        <Link
          to={`/feeds/${feed.slug}`}
          className="text-sm font-semibold text-foreground hover:text-primary truncate block"
        >
          {feed.title}
        </Link>
        <p className="text-xs text-muted-foreground truncate">
          {feed.episodeCount} episodes
          {feed.lastRefreshed && (
            <span className="ml-2">
              Updated {new Date(feed.lastRefreshed).toLocaleDateString()}
            </span>
          )}
        </p>
      </div>
      <div className="flex items-center gap-1 sm:gap-2 shrink-0">
        <CopyButton text={feed.feedUrl} hideLabelOnMobile />
        <button
          onClick={() => onRefresh(feed.slug)}
          disabled={isRefreshing}
          className="sm:hidden inline-flex items-center justify-center h-8 w-8 rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          title={isRefreshing ? 'Refreshing' : 'Refresh feed'}
          aria-label={isRefreshing ? 'Refreshing' : 'Refresh feed'}
        >
          <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
        <div className="hidden sm:block">
          <DropdownMenu
            triggerLabel={
              <>
                <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
                <span className="text-xs">{isRefreshing ? 'Refreshing' : 'Refresh'}</span>
              </>
            }
            triggerClassName="inline-flex items-center justify-center gap-1.5 h-8 px-2 rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
            disabled={isRefreshing}
            title={isRefreshing ? 'Refreshing' : 'Refresh feed'}
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
        </div>
        <button
          onClick={() => onDelete(feed.slug)}
          className="inline-flex items-center justify-center gap-1.5 h-8 w-8 sm:w-auto sm:px-2 rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 transition-colors"
          title="Delete feed"
          aria-label="Delete feed"
        >
          <Trash2 className="w-4 h-4" />
          <span className="hidden sm:inline text-xs">Delete</span>
        </button>
      </div>
    </div>
  );
}

export default FeedListItem;
