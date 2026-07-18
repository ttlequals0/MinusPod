import { useState, useMemo } from 'react';
import { Pencil } from 'lucide-react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getFeed, feedsQueryOptions, getEpisodes, refreshFeed, updateFeed, reprocessAllEpisodes, ReprocessAllResult, bulkEpisodeAction, BulkAction, UpdateFeedPayload } from '../api/feeds';
import type { BulkActionResult } from '../api/types';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import { sortFeeds, FeedSortBy, DASHBOARD_SORT_KEY, DEFAULT_FEED_SORT } from '../utils/feedSort';
import PrevNextLink from '../components/PrevNextLink';
import Artwork from '../components/Artwork';
import CopyButton from '../components/CopyButton';
import DropdownMenu from '../components/DropdownMenu';
import EpisodeList from '../components/EpisodeList';
import LoadingSpinner from '../components/LoadingSpinner';
import { Pagination } from '../components/Pagination';
import { feedDisplayTitle } from '../utils/feedTitle';
import FeedSettingsPanel from './feeds/FeedSettingsPanel';
import FeedStatsCards from './feeds/FeedStatsCards';
import PodcastAdDistributionPanel from './feeds/PodcastAdDistributionPanel';
import CueTemplatesPanel from './feeds/CueTemplatesPanel';
import { formatStorage } from './settings/settingsUtils';
import { formatDateTime } from '../utils/format';
import { stripHtml } from '../utils/stripHtml';
import { btnDestructive, btnGhost, btnPrimary, btnSecondary } from '../components/buttonStyles';
import { Modal } from '../components/Modal';

function reprocessModeLabel(mode: string): string {
  if (mode === 'full') return 'AI Only';
  if (mode === 'llm') return 'Re-detect Ads';
  return 'Patterns + AI';
}

function reprocessModeDescription(mode: string): string {
  if (mode === 'full') return 'Fresh analysis without pattern database';
  if (mode === 'llm') return 'Reuses saved transcripts (skips re-transcription); re-cuts audio';
  return 'Uses learned patterns for faster ad detection';
}

// Queued/skipped stats grid shared by the reprocess and bulk-action result
// modals (only the left-cell label differs).
function ResultStatsGrid({ queued, skipped, queuedLabel }: {
  queued: number;
  skipped: number;
  queuedLabel: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-4 text-center mb-4">
      <div className="p-3 rounded-lg bg-green-500/10">
        <p className="text-2xl font-bold text-success">{queued}</p>
        <p className="text-xs text-muted-foreground">{queuedLabel}</p>
      </div>
      <div className="p-3 rounded-lg bg-yellow-500/10">
        <p className="text-2xl font-bold text-yellow-600 dark:text-yellow-400">{skipped}</p>
        <p className="text-xs text-muted-foreground">Skipped</p>
      </div>
    </div>
  );
}

function reprocessModeVerb(mode: string): string {
  if (mode === 'full') return 'full AI';
  if (mode === 'llm') return 'transcript-reuse';
  return 'pattern-assisted';
}

function FeedDetail() {
  const { slug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [showReprocessConfirm, setShowReprocessConfirm] = useState(false);
  const [selectedReprocessMode, setSelectedReprocessMode] = useState<'reprocess' | 'full' | 'llm'>('reprocess');
  const [reprocessResult, setReprocessResult] = useState<ReprocessAllResult | null>(null);

  // Pagination state
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [statusFilter, setStatusFilter] = useState('all');
  const [sortBy, setSortBy] = useState('published_at');
  const [sortDir, setSortDir] = useState('desc');

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [bulkResult, setBulkResult] = useState<BulkActionResult | null>(null);

  const { data: feed, isLoading: feedLoading, error: feedError } = useQuery({
    queryKey: ['feed', slug],
    queryFn: () => getFeed(slug!),
    enabled: !!slug,
  });

  // Prev/next nav across feeds (issue #417 follow-up). Reuse the dashboard's
  // cached list and its sort so adjacency matches the list the user clicked from.
  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const [feedSortBy] = useLocalStorageState<FeedSortBy>(DASHBOARD_SORT_KEY, DEFAULT_FEED_SORT);
  const { prevFeed, nextFeed } = useMemo(() => {
    if (!feeds || !slug) return { prevFeed: null, nextFeed: null };
    const ordered = sortFeeds(feeds, feedSortBy);
    const i = ordered.findIndex((f) => f.slug === slug);
    if (i === -1) return { prevFeed: null, nextFeed: null };
    return { prevFeed: ordered[i - 1] ?? null, nextFeed: ordered[i + 1] ?? null };
  }, [feeds, feedSortBy, slug]);
  const prevLabel = feedSortBy === 'recent' ? 'Newer' : 'Prev';
  const nextLabel = feedSortBy === 'recent' ? 'Older' : 'Next';

  const { data: episodesData, isLoading: episodesLoading } = useQuery({
    queryKey: ['episodes', slug, page, pageSize, statusFilter, sortBy, sortDir],
    queryFn: () => getEpisodes(slug!, {
      limit: pageSize,
      offset: (page - 1) * pageSize,
      status: statusFilter,
      sortBy,
      sortDir,
    }),
    enabled: !!slug,
  });

  const episodes = episodesData?.episodes ?? [];
  const totalEpisodes = episodesData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalEpisodes / pageSize));

  const refreshMutation = useMutation({
    mutationFn: (opts?: { force?: boolean }) => refreshFeed(slug!, opts),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (data: UpdateFeedPayload) => updateFeed(slug!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      setIsEditingTitle(false);
    },
  });

  const reprocessAllMutation = useMutation({
    mutationFn: (mode: 'reprocess' | 'full' | 'llm') => reprocessAllEpisodes(slug!, mode),
    onSuccess: (result) => {
      setReprocessResult(result);
      setShowReprocessConfirm(false);
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
  });

  const bulkMutation = useMutation({
    mutationFn: ({ action }: { action: BulkAction }) =>
      bulkEpisodeAction(slug!, Array.from(selectedIds), action),
    onSuccess: (result) => {
      setBulkResult(result);
      setSelectedIds(new Set());
      setShowBulkDeleteConfirm(false);
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
  });

  const closeReprocessModal = () => {
    setShowReprocessConfirm(false);
    setReprocessResult(null);
    reprocessAllMutation.reset();
  };

  const startEditingTitle = () => {
    setEditTitle(feed?.titleOverride || '');
    setIsEditingTitle(true);
  };

  const saveTitleEdit = () => {
    updateMutation.mutate({ titleOverride: editTitle.trim() || null });
  };

  const handleToggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      const selectable = episodes.filter(ep => ep.status !== 'processing').map(ep => ep.id);
      setSelectedIds(new Set(selectable));
    } else {
      setSelectedIds(new Set());
    }
  };

  const handlePageSizeChange = (newSize: number) => {
    setPageSize(newSize);
    setPage(1);
    setSelectedIds(new Set());
  };

  const handlePageChange = (newPage: number) => {
    setPage(newPage);
    setSelectedIds(new Set());
  };

  // Bulk-action eligibility: count per-action so a mixed selection still
  // surfaces actionable buttons (backend skips ineligible rows).
  const selectedEpisodes = episodes.filter(ep => selectedIds.has(ep.id));
  const discoveredCount = selectedEpisodes.filter(ep => ep.status === 'discovered').length;
  const processedCount = selectedEpisodes.filter(ep =>
    ['completed', 'failed', 'permanently_failed', 'deferred'].includes(ep.status)
  ).length;
  const hasSelection = selectedIds.size > 0;

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

  // Hover feedback only when the artwork is actually a link (#521).
  const feedArtwork = (
    <Artwork
      src={feed.artworkUrl || `/api/v1/feeds/${slug}/artwork`}
      alt={feed.title}
      className={`w-full h-full object-cover rounded-lg${
        feed.websiteUrl ? ' hover:opacity-90 transition-opacity' : ''}`}
    />
  );

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4">
        <Link to="/" className="text-primary hover:underline inline-block">
          Back to Dashboard
        </Link>
        {feeds && feeds.length > 1 && (
          <nav className="flex items-center gap-1.5" aria-label="Adjacent feeds">
            <PrevNextLink
              side="prev"
              label={prevLabel}
              to={prevFeed ? `/feeds/${prevFeed.slug}` : null}
              title={prevFeed ? `${prevLabel} feed: ${feedDisplayTitle(prevFeed)}` : `No ${prevLabel.toLowerCase()} feed`}
            />
            <PrevNextLink
              side="next"
              label={nextLabel}
              to={nextFeed ? `/feeds/${nextFeed.slug}` : null}
              title={nextFeed ? `${nextLabel} feed: ${feedDisplayTitle(nextFeed)}` : `No ${nextLabel.toLowerCase()} feed`}
            />
          </nav>
        )}
      </div>

      <div className="bg-card rounded-lg border border-border p-6 mb-6">
        <div className="flex flex-col sm:flex-row gap-6">
          <div className="w-32 h-32 shrink-0 mx-auto sm:mx-0">
            {feed.websiteUrl ? (
              <a
                href={feed.websiteUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={`Open the ${feed.title} website`}
                className="block w-full h-full rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
              >
                {feedArtwork}
              </a>
            ) : feedArtwork}
          </div>
          <div className="flex-1 min-w-0">
            {isEditingTitle ? (
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    type="text"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') saveTitleEdit();
                      if (e.key === 'Escape') setIsEditingTitle(false);
                    }}
                    placeholder={feed.title}
                    maxLength={500}
                    autoFocus
                    className="flex-1 min-w-0 px-2 py-1 text-lg font-semibold bg-secondary border border-border rounded focus:outline-hidden focus:ring-2 focus:ring-ring"
                  />
                  <button
                    onClick={saveTitleEdit}
                    disabled={updateMutation.isPending}
                    className={`px-2 py-1 text-xs ${btnPrimary} rounded disabled:opacity-50`}
                  >
                    {updateMutation.isPending ? 'Saving...' : 'Save'}
                  </button>
                  <button
                    onClick={() => setIsEditingTitle(false)}
                    className="px-2 py-1 text-xs bg-muted text-muted-foreground rounded hover:bg-accent"
                  >
                    Cancel
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Shown to subscribers in podcast apps. Leave blank to use the source title
                  {!feed.titleOverride && ` ("${feed.title}")`}.
                </p>
              </div>
            ) : (
              <div className="flex items-start gap-2">
                <h1 className="text-2xl font-bold text-foreground min-w-0 break-words">
                  {feedDisplayTitle(feed)}
                </h1>
                {feed.titleOverride && (
                  <span className="mt-1.5 shrink-0 px-2 py-0.5 rounded text-xs font-medium bg-blue-500/15 text-blue-700 dark:text-blue-400">
                    Custom
                  </span>
                )}
                <button
                  onClick={startEditingTitle}
                  aria-label="Edit feed title"
                  title="Edit feed title"
                  className={`mt-1.5 shrink-0 p-1 rounded ${btnGhost} transition-colors`}
                >
                  <Pencil className="w-4 h-4" />
                </button>
              </div>
            )}
            {feed.description && (
              <p className="text-muted-foreground mt-2 line-clamp-3">{stripHtml(feed.description)}</p>
            )}
            <div className="mt-4 flex flex-wrap gap-4 text-sm text-muted-foreground">
              <span>{feed.episodeCount} episodes</span>
              {feed.lastRefreshed && (
                <span>Updated {formatDateTime(feed.lastRefreshed)}</span>
              )}
              {feed.lastRefreshError && (
                <span
                  className="text-warning"
                  title={feed.lastRefreshError}
                >
                  Refresh failing since {formatDateTime(feed.lastRefreshErrorAt ?? null)}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="mt-6 pt-4 border-t border-border flex flex-wrap gap-4 items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="hidden sm:inline text-sm text-muted-foreground shrink-0">Feed URL:</span>
            <code className="hidden sm:block text-sm bg-secondary px-2 py-1 rounded truncate min-w-0">
              {feed.feedUrl}
            </code>
            <CopyButton
              text={feed.feedUrl}
              label="Copy Feed URL"
              className={`px-4 py-2 sm:px-0 sm:py-0 sm:p-1.5 gap-2 ${btnSecondary} sm:bg-transparent sm:text-muted-foreground sm:hover:bg-accent`}
              copiedClassName="text-green-500 bg-green-500/10 sm:bg-transparent"
              labelClassName="text-sm"
            />
          </div>
          <div className="flex gap-2">
            <DropdownMenu
              triggerLabel={reprocessAllMutation.isPending ? 'Queuing...' : 'Reprocess All'}
              triggerClassName={`px-3 py-1.5 sm:px-4 sm:py-2 text-sm rounded ${btnSecondary} disabled:opacity-50 transition-colors flex items-center gap-2 whitespace-nowrap`}
              disabled={reprocessAllMutation.isPending}
              title="Reprocess all processed episodes"
              align="left"
              items={[
                {
                  title: 'Patterns + AI',
                  subtitle: 'Use learned patterns for faster detection',
                  onClick: () => {
                    setSelectedReprocessMode('reprocess');
                    setShowReprocessConfirm(true);
                  },
                },
                {
                  title: 'AI Only',
                  subtitle: 'Fresh analysis without patterns',
                  onClick: () => {
                    setSelectedReprocessMode('full');
                    setShowReprocessConfirm(true);
                  },
                },
                {
                  title: 'Re-detect Ads',
                  subtitle: 'Keep transcripts, skip re-transcription',
                  onClick: () => {
                    setSelectedReprocessMode('llm');
                    setShowReprocessConfirm(true);
                  },
                },
              ]}
            />
            <DropdownMenu
              triggerLabel={refreshMutation.isPending ? 'Refreshing...' : 'Refresh Feed'}
              triggerClassName={`px-3 py-1.5 sm:px-4 sm:py-2 text-sm rounded ${btnPrimary} disabled:opacity-50 transition-colors flex items-center gap-2 whitespace-nowrap`}
              disabled={refreshMutation.isPending}
              title="Refresh feed"
              items={[
                {
                  title: 'Refresh',
                  subtitle: 'Check for new episodes',
                  onClick: () => refreshMutation.mutate(undefined),
                },
                {
                  title: 'Force refresh',
                  subtitle: 'Bypass cache',
                  onClick: () => refreshMutation.mutate({ force: true }),
                },
              ]}
            />
          </div>
        </div>
      </div>

      {slug && <FeedStatsCards feed={feed} slug={slug} />}

      {slug && <FeedSettingsPanel feed={feed} slug={slug} />}

      {slug && <PodcastAdDistributionPanel slug={slug} />}

      {slug && <CueTemplatesPanel slug={slug} />}

      {/* Episodes header with status filter */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-xl font-semibold text-foreground">
          Episodes {totalEpisodes > 0 && <span className="text-muted-foreground font-normal text-base">({totalEpisodes})</span>}
        </h2>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); setSelectedIds(new Set()); }}
            className="px-2 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            <option value="all">All statuses</option>
            <option value="discovered">Discovered</option>
            <option value="pending">Pending</option>
            <option value="processing">Processing</option>
            <option value="processed">Completed</option>
            <option value="failed">Failed</option>
            <option value="permanently_failed">Permanently Failed</option>
            <option value="deferred">Queued (offline)</option>
          </select>
          <select
            value={`${sortBy}:${sortDir}`}
            onChange={(e) => {
              const [newSort, newDir] = e.target.value.split(':');
              setSortBy(newSort);
              setSortDir(newDir);
              setPage(1);
              setSelectedIds(new Set());
            }}
            className="px-2 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            <option value="published_at:desc">Newest First</option>
            <option value="published_at:asc">Oldest First</option>
            <option value="episode_number:desc">Episode # (High-Low)</option>
            <option value="episode_number:asc">Episode # (Low-High)</option>
          </select>
        </div>
      </div>

      {/* Bulk action toolbar */}
      {hasSelection && (
        <div className="mb-4 p-3 bg-secondary/50 rounded-lg border border-border flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{selectedIds.size} selected</span>
          <div className="flex flex-wrap items-center gap-2 ml-auto">
            {discoveredCount > 0 && (
              <button
                onClick={() => bulkMutation.mutate({ action: 'process' })}
                disabled={bulkMutation.isPending}
                className={`px-3 py-1.5 text-sm rounded ${btnPrimary} disabled:opacity-50 whitespace-nowrap min-w-[8rem] text-center`}
              >
                {bulkMutation.isPending ? 'Processing...' : `Process (${discoveredCount})`}
              </button>
            )}
            {processedCount > 0 && (
              <>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess' })}
                  disabled={bulkMutation.isPending}
                  className={`px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 whitespace-nowrap min-w-[8rem] text-center`}
                >
                  Reprocess ({processedCount})
                </button>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess_full' })}
                  disabled={bulkMutation.isPending}
                  className={`px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 whitespace-nowrap min-w-[8rem] text-center`}
                >
                  Full Reprocess ({processedCount})
                </button>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess_llm' })}
                  disabled={bulkMutation.isPending}
                  className={`px-3 py-1.5 text-sm rounded ${btnSecondary} disabled:opacity-50 whitespace-nowrap min-w-[8rem] text-center`}
                  title="Re-detect ads using existing transcripts (skips re-transcription)"
                >
                  Re-detect Ads ({processedCount})
                </button>
                <button
                  onClick={() => setShowBulkDeleteConfirm(true)}
                  disabled={bulkMutation.isPending}
                  className={`px-3 py-1.5 text-sm rounded ${btnDestructive} disabled:opacity-50 whitespace-nowrap min-w-[8rem] text-center`}
                >
                  Delete ({processedCount})
                </button>
              </>
            )}
            {discoveredCount === 0 && processedCount === 0 && (
              <span className="text-xs text-muted-foreground">No actionable items in selection (pending/processing rows skip)</span>
            )}
            <button
              onClick={() => setSelectedIds(new Set())}
              className="px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {episodesLoading ? (
        <LoadingSpinner />
      ) : (
        <EpisodeList
          episodes={episodes}
          feedSlug={slug!}
          feedArtworkUrl={feed.artworkUrl}
          selectedIds={selectedIds}
          onToggle={handleToggleSelect}
          onSelectAll={handleSelectAll}
        />
      )}

      {/* Pagination controls (shared Pagination renders nothing at 1 page) */}
      <Pagination page={page} totalPages={totalPages} total={totalEpisodes} onPage={handlePageChange} />
      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-end gap-2">
          <span className="text-sm text-muted-foreground">Per page:</span>
          {[25, 50, 100, 500].map(size => (
            <button
              key={size}
              onClick={() => handlePageSizeChange(size)}
              className={`px-2 py-1 text-xs rounded ${
                pageSize === size
                  ? 'bg-primary text-primary-foreground'
                  : btnSecondary
              }`}
            >
              {size}
            </button>
          ))}
        </div>
      )}

      {/* Reprocess All Confirmation Modal */}
      {showReprocessConfirm && (
        <Modal onClose={() => setShowReprocessConfirm(false)} panelClassName="max-w-md w-full">
          <div className="p-6">
            <h2 className="text-xl font-semibold text-foreground mb-4">
              Reprocess All Episodes
            </h2>
            <div className="mb-4 p-3 rounded-lg bg-accent/50">
              <p className="text-sm font-medium text-foreground">
                Mode: {reprocessModeLabel(selectedReprocessMode)}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {reprocessModeDescription(selectedReprocessMode)}
              </p>
            </div>
            <p className="text-sm text-muted-foreground mb-4">
              {selectedReprocessMode === 'llm'
                ? 'This will queue all processed episodes that have a saved transcript. The transcript is reused (no re-transcription); audio is re-analyzed and re-cut. Episodes without a transcript are skipped.'
                : 'This will queue all processed episodes for reprocessing. Existing processed audio files will be deleted and episodes will be re-transcribed and re-analyzed.'}
            </p>
            <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-6">
              This operation cannot be undone. Episodes currently processing will be skipped.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowReprocessConfirm(false)}
                className={`px-4 py-2 rounded ${btnSecondary}`}
              >
                Cancel
              </button>
              <button
                onClick={() => reprocessAllMutation.mutate(selectedReprocessMode)}
                disabled={reprocessAllMutation.isPending}
                className={`px-4 py-2 rounded ${btnDestructive} disabled:opacity-50`}
              >
                {reprocessAllMutation.isPending ? 'Queuing...' : 'Reprocess All'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Reprocess Results Modal */}
      {reprocessResult && (
        <Modal onClose={closeReprocessModal} panelClassName="max-w-md w-full">
          <div className="p-6">
            <h2 className="text-xl font-semibold text-foreground mb-4">Reprocess Queued</h2>
            <p className="text-xs text-muted-foreground mb-4">
              Mode: {reprocessModeLabel(reprocessResult.mode)}
            </p>
            <ResultStatsGrid queued={reprocessResult.queued} skipped={reprocessResult.skipped} queuedLabel="Queued" />
            {reprocessResult.queued > 0 && (
              <p className="text-sm text-muted-foreground mb-4">
                {reprocessResult.queued} episodes have been queued for {reprocessModeVerb(reprocessResult.mode)} reprocessing. They will be processed in the background.
              </p>
            )}
            <button
              onClick={closeReprocessModal}
              className={`w-full px-4 py-2 rounded ${btnPrimary}`}
            >
              Done
            </button>
          </div>
        </Modal>
      )}

      {/* Reprocess Error Modal */}
      {reprocessAllMutation.error && (
        <Modal onClose={closeReprocessModal} panelClassName="max-w-md w-full">
          <div className="p-6">
            <h2 className="text-xl font-semibold text-destructive mb-4">Reprocess Failed</h2>
            <p className="text-sm text-muted-foreground mb-4">
              {(reprocessAllMutation.error as Error).message}
            </p>
            <button
              onClick={closeReprocessModal}
              className={`w-full px-4 py-2 rounded ${btnPrimary}`}
            >
              Close
            </button>
          </div>
        </Modal>
      )}

      {/* Bulk Delete Confirmation Modal */}
      {showBulkDeleteConfirm && (
        <Modal onClose={() => setShowBulkDeleteConfirm(false)} panelClassName="max-w-md w-full">
          <div className="p-6">
            <h2 className="text-xl font-semibold text-foreground mb-4">
              Delete {selectedIds.size} Episode{selectedIds.size > 1 ? 's' : ''}
            </h2>
            <p className="text-sm text-muted-foreground mb-4">
              This will delete processed audio files and reset selected episodes to discovered status. Episode records and processing history are preserved.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowBulkDeleteConfirm(false)}
                className={`px-4 py-2 rounded ${btnSecondary}`}
              >
                Cancel
              </button>
              <button
                onClick={() => bulkMutation.mutate({ action: 'delete' })}
                disabled={bulkMutation.isPending}
                className={`px-4 py-2 rounded ${btnDestructive} disabled:opacity-50`}
              >
                {bulkMutation.isPending ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </Modal>
      )}

      {/* Bulk Action Result Modal */}
      {bulkResult && (
        <Modal onClose={() => setBulkResult(null)} panelClassName="max-w-md w-full">
          <div className="p-6">
            <h2 className="text-xl font-semibold text-foreground mb-4">Bulk Action Complete</h2>
            <ResultStatsGrid queued={bulkResult.queued} skipped={bulkResult.skipped} queuedLabel="Actioned" />
            {bulkResult.freedMb > 0 && (
              <p className="text-sm text-muted-foreground mb-4">
                Freed {formatStorage(bulkResult.freedMb)} of disk space.
              </p>
            )}
            {bulkResult.errors.length > 0 && (
              <div className="mb-4 p-3 rounded-lg bg-destructive/10">
                <p className="text-sm text-destructive">{bulkResult.errors.length} error(s)</p>
              </div>
            )}
            <button
              onClick={() => setBulkResult(null)}
              className={`w-full px-4 py-2 rounded ${btnPrimary}`}
            >
              Done
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default FeedDetail;
