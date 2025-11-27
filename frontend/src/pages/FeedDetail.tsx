import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getFeed, getEpisodes, refreshFeed } from '../api/feeds';
import EpisodeList from '../components/EpisodeList';
import LoadingSpinner from '../components/LoadingSpinner';

function FeedDetail() {
  const { slug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();

  const { data: feed, isLoading: feedLoading, error: feedError } = useQuery({
    queryKey: ['feed', slug],
    queryFn: () => getFeed(slug!),
    enabled: !!slug,
  });

  const { data: episodes, isLoading: episodesLoading } = useQuery({
    queryKey: ['episodes', slug],
    queryFn: () => getEpisodes(slug!),
    enabled: !!slug,
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshFeed(slug!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
  });

  const copyFeedUrl = async () => {
    if (feed?.feedUrl) {
      try {
        await navigator.clipboard.writeText(feed.feedUrl);
      } catch {
        const input = document.createElement('input');
        input.value = feed.feedUrl;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
      }
    }
  };

  if (feedLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  if (feedError || !feed) {
    return (
      <div className="text-center py-12">
        <p className="text-destructive">Failed to load feed</p>
        <Link to="/" className="text-primary hover:underline mt-2 inline-block">
          Back to Dashboard
        </Link>
      </div>
    );
  }

  return (
    <div>
      <Link to="/" className="text-primary hover:underline mb-4 inline-block">
        Back to Dashboard
      </Link>

      <div className="bg-card rounded-lg border border-border p-6 mb-6">
        <div className="flex gap-6">
          <div className="w-32 h-32 flex-shrink-0">
            <img
              src={`/api/v1/feeds/${slug}/artwork`}
              alt={feed.title}
              className="w-full h-full object-cover rounded-lg"
              onError={(e) => {
                (e.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="%239ca3af"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>';
              }}
            />
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-foreground">{feed.title}</h1>
            {feed.description && (
              <p className="text-muted-foreground mt-2 line-clamp-3">{feed.description}</p>
            )}
            <div className="mt-4 flex flex-wrap gap-4 text-sm text-muted-foreground">
              <span>{feed.episodeCount} episodes</span>
              {feed.lastRefreshed && (
                <span>Updated {new Date(feed.lastRefreshed).toLocaleDateString()}</span>
              )}
            </div>
          </div>
        </div>

        <div className="mt-6 pt-4 border-t border-border flex flex-wrap gap-4 items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground hidden sm:inline">Feed URL:</span>
            <code className="text-sm bg-secondary px-2 py-1 rounded truncate max-w-md hidden sm:block">
              {feed.feedUrl}
            </code>
            <button
              onClick={copyFeedUrl}
              className="flex items-center gap-2 px-3 py-1.5 sm:p-1 rounded sm:rounded-none bg-secondary sm:bg-transparent text-muted-foreground hover:text-foreground transition-colors"
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
              <span className="text-sm sm:hidden">Copy Feed URL</span>
            </button>
          </div>
          <button
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
            className="px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {refreshMutation.isPending ? 'Refreshing...' : 'Refresh Feed'}
          </button>
        </div>
      </div>

      <h2 className="text-xl font-semibold text-foreground mb-4">Episodes</h2>
      {episodesLoading ? (
        <LoadingSpinner />
      ) : (
        <EpisodeList episodes={episodes || []} feedSlug={slug!} />
      )}
    </div>
  );
}

export default FeedDetail;
