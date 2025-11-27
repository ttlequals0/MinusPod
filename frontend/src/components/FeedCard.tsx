import { Link } from 'react-router-dom';
import { Feed } from '../api/types';

interface FeedCardProps {
  feed: Feed;
  onRefresh: (slug: string) => void;
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
        <CopyFeedUrl feedUrl={feed.feedUrl} />
        <div className="flex gap-2">
          <button
            onClick={() => onRefresh(feed.slug)}
            disabled={isRefreshing}
            className="px-3 py-1 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {isRefreshing ? 'Refreshing...' : 'Refresh'}
          </button>
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

function CopyFeedUrl({ feedUrl }: { feedUrl: string }) {
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(feedUrl);
    } catch {
      const input = document.createElement('input');
      input.value = feedUrl;
      document.body.appendChild(input);
      input.select();
      document.execCommand('copy');
      document.body.removeChild(input);
    }
  };

  return (
    <button
      onClick={handleCopy}
      className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
      title="Copy feed URL"
    >
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
        />
      </svg>
      Copy URL
    </button>
  );
}

export default FeedCard;
