import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getFeeds, refreshFeed, refreshAllFeeds, deleteFeed, importOpml, OpmlImportResult } from '../api/feeds';
import FeedCard from '../components/FeedCard';
import FeedListItem from '../components/FeedListItem';
import LoadingSpinner from '../components/LoadingSpinner';

function Dashboard() {
  const queryClient = useQueryClient();
  const [refreshingSlug, setRefreshingSlug] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [showOpmlModal, setShowOpmlModal] = useState(false);
  const [opmlResult, setOpmlResult] = useState<OpmlImportResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>(() => {
    const stored = localStorage.getItem('dashboardViewMode');
    return stored === 'list' ? 'list' : 'grid';
  });
  const [sortBy, setSortBy] = useState<'recent' | 'title'>(() => {
    const stored = localStorage.getItem('dashboardSortBy');
    return stored === 'title' ? 'title' : 'recent';
  });

  useEffect(() => {
    localStorage.setItem('dashboardViewMode', viewMode);
  }, [viewMode]);

  useEffect(() => {
    localStorage.setItem('dashboardSortBy', sortBy);
  }, [sortBy]);

  const { data: feeds, isLoading, error } = useQuery({
    queryKey: ['feeds'],
    queryFn: getFeeds,
  });

  const refreshMutation = useMutation({
    mutationFn: refreshFeed,
    onMutate: (slug) => setRefreshingSlug(slug),
    onSettled: () => {
      setRefreshingSlug(null);
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
  });

  const refreshAllMutation = useMutation({
    mutationFn: refreshAllFeeds,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteFeed,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      setDeleteConfirm(null);
    },
  });

  const opmlMutation = useMutation({
    mutationFn: importOpml,
    onSuccess: (result) => {
      setOpmlResult(result);
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
    },
  });

  const handleOpmlImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      opmlMutation.mutate(file);
    }
    // Reset input so same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const closeOpmlModal = () => {
    setShowOpmlModal(false);
    setOpmlResult(null);
    opmlMutation.reset();
  };

  const handleDelete = (slug: string) => {
    if (deleteConfirm === slug) {
      deleteMutation.mutate(slug);
    } else {
      setDeleteConfirm(slug);
      setTimeout(() => setDeleteConfirm(null), 3000);
    }
  };

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
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold text-foreground">Feeds</h1>
        <div className="flex gap-2 items-center">
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
          <button
            onClick={() => refreshAllMutation.mutate()}
            disabled={refreshAllMutation.isPending}
            className="p-2 sm:px-4 sm:py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
            title="Refresh All"
          >
            <svg className="w-5 h-5 sm:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            <span className="hidden sm:inline">{refreshAllMutation.isPending ? 'Refreshing...' : 'Refresh All'}</span>
          </button>
          <button
            onClick={() => setShowOpmlModal(true)}
            className="p-2 sm:px-4 sm:py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 transition-colors"
            title="Import OPML"
          >
            <svg className="w-5 h-5 sm:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            <span className="hidden sm:inline">Import OPML</span>
          </button>
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

      {/* Hidden file input for OPML import */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleOpmlImport}
        accept=".opml,.xml"
        className="hidden"
      />

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
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[...feeds].sort((a, b) => {
            if (sortBy === 'recent') {
              const dateA = a.lastEpisodeDate ? new Date(a.lastEpisodeDate).getTime() : 0;
              const dateB = b.lastEpisodeDate ? new Date(b.lastEpisodeDate).getTime() : 0;
              return dateB - dateA;
            }
            return a.title.localeCompare(b.title, undefined, { sensitivity: 'base' });
          }).map((feed) => (
            <FeedCard
              key={feed.slug}
              feed={feed}
              onRefresh={(slug) => refreshMutation.mutate(slug)}
              onDelete={handleDelete}
              isRefreshing={refreshingSlug === feed.slug}
            />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {[...feeds].sort((a, b) => {
            if (sortBy === 'recent') {
              const dateA = a.lastEpisodeDate ? new Date(a.lastEpisodeDate).getTime() : 0;
              const dateB = b.lastEpisodeDate ? new Date(b.lastEpisodeDate).getTime() : 0;
              return dateB - dateA;
            }
            return a.title.localeCompare(b.title, undefined, { sensitivity: 'base' });
          }).map((feed) => (
            <FeedListItem
              key={feed.slug}
              feed={feed}
              onRefresh={(slug) => refreshMutation.mutate(slug)}
              onDelete={handleDelete}
              isRefreshing={refreshingSlug === feed.slug}
            />
          ))}
        </div>
      )}

      {deleteConfirm && (
        <div className="fixed bottom-4 right-4 bg-card border border-border rounded-lg p-4 shadow-lg">
          <p className="text-sm text-foreground">Click delete again to confirm</p>
        </div>
      )}

      {/* OPML Import Modal */}
      {showOpmlModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full max-h-[80vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex justify-between items-start mb-4">
                <h2 className="text-xl font-semibold text-foreground">Import OPML</h2>
                <button
                  onClick={closeOpmlModal}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {!opmlResult && !opmlMutation.isPending && !opmlMutation.error && (
                <div className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    Import podcast feeds from an OPML file. This is commonly exported from podcast apps.
                  </p>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="w-full px-4 py-8 border-2 border-dashed border-border rounded-lg hover:border-primary hover:bg-accent/50 transition-colors text-center"
                  >
                    <svg className="w-8 h-8 mx-auto mb-2 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                    </svg>
                    <p className="text-sm text-foreground font-medium">Click to select OPML file</p>
                    <p className="text-xs text-muted-foreground mt-1">.opml or .xml files supported</p>
                  </button>
                </div>
              )}

              {opmlMutation.isPending && (
                <div className="text-center py-8">
                  <LoadingSpinner />
                  <p className="text-sm text-muted-foreground mt-4">Importing feeds...</p>
                </div>
              )}

              {opmlMutation.error && (
                <div className="space-y-4">
                  <div className="p-4 rounded-lg bg-destructive/10 text-destructive">
                    {(opmlMutation.error as Error).message}
                  </div>
                  <button
                    onClick={() => {
                      opmlMutation.reset();
                      fileInputRef.current?.click();
                    }}
                    className="w-full px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
                  >
                    Try Again
                  </button>
                </div>
              )}

              {opmlResult && (
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-4 text-center">
                    <div className="p-3 rounded-lg bg-green-500/10">
                      <p className="text-2xl font-bold text-green-600 dark:text-green-400">{opmlResult.imported}</p>
                      <p className="text-xs text-muted-foreground">Imported</p>
                    </div>
                    <div className="p-3 rounded-lg bg-yellow-500/10">
                      <p className="text-2xl font-bold text-yellow-600 dark:text-yellow-400">{opmlResult.skipped}</p>
                      <p className="text-xs text-muted-foreground">Skipped</p>
                    </div>
                    <div className="p-3 rounded-lg bg-red-500/10">
                      <p className="text-2xl font-bold text-red-600 dark:text-red-400">{opmlResult.failed}</p>
                      <p className="text-xs text-muted-foreground">Failed</p>
                    </div>
                  </div>

                  {opmlResult.feeds.imported.length > 0 && (
                    <div>
                      <p className="text-sm font-medium text-foreground mb-2">Imported feeds:</p>
                      <ul className="text-xs text-muted-foreground space-y-1 max-h-32 overflow-y-auto">
                        {opmlResult.feeds.imported.slice(0, 10).map((feed, i) => (
                          <li key={i} className="truncate">{feed.slug}</li>
                        ))}
                        {opmlResult.feeds.imported.length > 10 && (
                          <li className="text-primary">+{opmlResult.feeds.imported.length - 10} more</li>
                        )}
                      </ul>
                    </div>
                  )}

                  {opmlResult.feeds.failed.length > 0 && (
                    <div>
                      <p className="text-sm font-medium text-destructive mb-2">Failed imports:</p>
                      <ul className="text-xs text-muted-foreground space-y-1 max-h-32 overflow-y-auto">
                        {opmlResult.feeds.failed.slice(0, 5).map((feed, i) => (
                          <li key={i} className="truncate" title={feed.error}>{feed.url}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <button
                    onClick={closeOpmlModal}
                    className="w-full px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
                  >
                    Done
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default Dashboard;
