import { useState, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { feedsQueryOptions, refreshFeed, refreshAllFeeds, deleteFeed } from '../api/feeds';
import DropdownMenu from '../components/DropdownMenu';
import FeedCard from '../components/FeedCard';
import FeedListItem from '../components/FeedListItem';
import LoadingSpinner from '../components/LoadingSpinner';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import { sortFeeds, FeedSortBy, DASHBOARD_SORT_KEY, DEFAULT_FEED_SORT } from '../utils/feedSort';
import { formatDateTime } from '../utils/format';

function Dashboard() {
  const queryClient = useQueryClient();
  const [refreshingSlug, setRefreshingSlug] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [viewMode, setViewMode] = useLocalStorageState<'grid' | 'list'>('dashboardViewMode', 'grid');
  const [sortBy, setSortBy] = useLocalStorageState<FeedSortBy>(DASHBOARD_SORT_KEY, DEFAULT_FEED_SORT);
  const [actionError, setActionError] = useState<string | null>(null);
  const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data, isLoading, error } = useQuery(feedsQueryOptions);
  const feeds = data?.feeds;
  const lastRefreshCompletedAt = data?.lastRefreshCompletedAt ?? null;

  const refreshMutation = useMutation({
    mutationFn: ({ slug, options }: { slug: string; options?: { force?: boolean } }) =>
      refreshFeed(slug, options),
    onMutate: ({ slug }) => { setRefreshingSlug(slug); setActionError(null); },
    onError: (err) => setActionError((err as Error).message),
    onSettled: () => {
      setRefreshingSlug(null);
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
  });

  const refreshAllMutation = useMutation({
    mutationFn: refreshAllFeeds,
    onMutate: () => setActionError(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
    onError: (err) => setActionError((err as Error).message),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteFeed,
    onMutate: () => setActionError(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      setDeleteConfirm(null);
    },
    onError: (err) => { setDeleteConfirm(null); setActionError((err as Error).message); },
  });

  const handleDelete = (slug: string) => {
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    if (deleteConfirm === slug) {
      deleteMutation.mutate(slug);
    } else {
      setDeleteConfirm(slug);
      deleteTimerRef.current = setTimeout(() => setDeleteConfirm(null), 3000);
    }
  };

  const handleRefresh = (slug: string, options?: { force?: boolean }) => {
    refreshMutation.mutate({ slug, options });
  };

  const sortedFeeds = useMemo(() => (feeds ? sortFeeds(feeds, sortBy) : []), [feeds, sortBy]);

  if (isLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <p className="text-destructive">Failed to load feeds</p>
        <p className="text-sm text-muted-foreground mt-2">{(error as Error).message}</p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-wrap justify-between items-center gap-y-2 mb-6">
        <div className="w-full sm:w-auto flex flex-wrap items-baseline gap-x-3">
          <h1 className="text-2xl font-bold text-foreground">Feeds</h1>
          {lastRefreshCompletedAt && (
            <span
              className="text-sm text-muted-foreground"
              title="When the last check of all feeds finished"
            >
              Updated {formatDateTime(lastRefreshCompletedAt)}
            </span>
          )}
        </div>
        <div className="flex gap-2 items-center shrink-0">
          <div className="flex gap-2 items-center overflow-x-auto no-scrollbar">
            <div className="flex border border-border rounded overflow-hidden">
              <button
                onClick={() => setViewMode('grid')}
                className={`p-2 transition-colors ${
                  viewMode === 'grid'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
                }`}
                aria-label="Grid view"
                title="Grid view"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                </svg>
              </button>
              <button
                onClick={() => setViewMode('list')}
                className={`p-2 transition-colors ${
                  viewMode === 'list'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
                }`}
                aria-label="List view"
                title="List view"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            </div>
            <div className="flex border border-border rounded overflow-hidden">
              <button
                onClick={() => setSortBy('recent')}
                className={`p-2 transition-colors ${
                  sortBy === 'recent'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
                }`}
                aria-label="Sort by recent"
                title="Sort by most recent episode"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </button>
              <button
                onClick={() => setSortBy('title')}
                className={`p-2 transition-colors ${
                  sortBy === 'title'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
                }`}
                aria-label="Sort by title"
                title="Sort alphabetically"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12" />
                </svg>
              </button>
            </div>
          </div>
          <DropdownMenu
            triggerLabel={
              <>
                <svg className="w-5 h-5 sm:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                <span className="hidden sm:inline">{refreshAllMutation.isPending ? 'Refreshing...' : 'Refresh All'}</span>
              </>
            }
            triggerClassName="p-2 sm:px-4 sm:py-2 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors flex items-center gap-2 whitespace-nowrap"
            disabled={refreshAllMutation.isPending}
            title="Refresh all feeds"
            items={[
              {
                title: 'Refresh All',
                subtitle: 'Check every feed for new episodes',
                onClick: () => refreshAllMutation.mutate(undefined),
              },
              {
                title: 'Force Refresh All',
                subtitle: 'Bypass cache on every feed',
                onClick: () => refreshAllMutation.mutate({ force: true }),
              },
            ]}
          />
          <Link
            to="/add"
            className="p-2 sm:px-4 sm:py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            title="Add Feed"
          >
            <svg className="w-5 h-5 sm:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            <span className="hidden sm:inline">Add Feed</span>
          </Link>
        </div>
      </div>

      {!feeds || feeds.length === 0 ? (
        <div className="text-center py-12 bg-card rounded-lg border border-border">
          <p className="text-muted-foreground mb-4">No feeds added yet</p>
          <Link
            to="/add"
            className="inline-block px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            Add Your First Feed
          </Link>
          <p className="text-sm text-muted-foreground mt-4">
            Find podcast RSS feeds at{' '}
            <a
              href="https://podcastindex.org/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              podcastindex.org
            </a>
          </p>
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
          {sortedFeeds.map((feed) => (
            <FeedCard
              key={feed.slug}
              feed={feed}
              onRefresh={handleRefresh}
              onDelete={handleDelete}
              isRefreshing={refreshingSlug === feed.slug}
            />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {sortedFeeds.map((feed) => (
            <FeedListItem
              key={feed.slug}
              feed={feed}
              onRefresh={handleRefresh}
              onDelete={handleDelete}
              isRefreshing={refreshingSlug === feed.slug}
            />
          ))}
        </div>
      )}

      {(deleteConfirm || actionError) && (
        <div className="fixed bottom-4 right-4 flex flex-col items-end gap-2">
          {actionError && (
            <div className="max-w-sm bg-destructive/10 border border-destructive text-destructive rounded-lg p-4 shadow-lg text-sm flex items-start gap-3">
              <span className="flex-1">{actionError}</span>
              <button
                onClick={() => setActionError(null)}
                aria-label="Dismiss error"
                className="shrink-0 text-destructive/70 hover:text-destructive"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}
          {deleteConfirm && (
            <div className="bg-card border border-border rounded-lg p-4 shadow-lg">
              <p className="text-sm text-foreground">Click delete again to confirm</p>
            </div>
          )}
        </div>
      )}

    </div>
  );
}

export default Dashboard;
