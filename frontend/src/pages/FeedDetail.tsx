import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getFeed, getEpisodes, refreshFeed, updateFeed, getNetworks, reprocessAllEpisodes, ReprocessAllResult, bulkEpisodeAction, BulkAction } from '../api/feeds';
import type { BulkActionResult } from '../api/types';
import EpisodeList from '../components/EpisodeList';
import LoadingSpinner from '../components/LoadingSpinner';
import { formatStorage } from './settings/settingsUtils';

function FeedDetail() {
  const { slug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const [isEditingNetwork, setIsEditingNetwork] = useState(false);
  const [showReprocessConfirm, setShowReprocessConfirm] = useState(false);
  const [showReprocessDropdown, setShowReprocessDropdown] = useState(false);
  const [selectedReprocessMode, setSelectedReprocessMode] = useState<'reprocess' | 'full'>('reprocess');
  const [reprocessResult, setReprocessResult] = useState<ReprocessAllResult | null>(null);
  const [editNetworkOverride, setEditNetworkOverride] = useState<string>('');
  const [editDaiPlatform, setEditDaiPlatform] = useState('');
  const [editAutoProcessOverride, setEditAutoProcessOverride] = useState<string>('global');
  const [editMaxEpisodes, setEditMaxEpisodes] = useState<string>('');

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

  const { data: networks } = useQuery({
    queryKey: ['networks'],
    queryFn: getNetworks,
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshFeed(slug!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (data: { networkIdOverride?: string | null; daiPlatform?: string; autoProcessOverride?: boolean | null; maxEpisodes?: number | null }) => updateFeed(slug!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      setIsEditingNetwork(false);
    },
  });

  const reprocessAllMutation = useMutation({
    mutationFn: (mode: 'reprocess' | 'full') => reprocessAllEpisodes(slug!, mode),
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

  const startEditingNetwork = () => {
    setEditNetworkOverride(feed?.networkIdOverride || '');
    setEditDaiPlatform(feed?.daiPlatform || '');
    if (feed?.autoProcessOverride === true) {
      setEditAutoProcessOverride('enable');
    } else if (feed?.autoProcessOverride === false) {
      setEditAutoProcessOverride('disable');
    } else {
      setEditAutoProcessOverride('global');
    }
    setEditMaxEpisodes(feed?.maxEpisodes ? String(feed.maxEpisodes) : '');
    setIsEditingNetwork(true);
  };

  const saveNetworkEdit = () => {
    let autoProcessOverride: boolean | null = null;
    if (editAutoProcessOverride === 'enable') {
      autoProcessOverride = true;
    } else if (editAutoProcessOverride === 'disable') {
      autoProcessOverride = false;
    }

    const maxEp = editMaxEpisodes ? parseInt(editMaxEpisodes, 10) : null;

    updateMutation.mutate({
      networkIdOverride: editNetworkOverride || null,
      daiPlatform: editDaiPlatform || undefined,
      autoProcessOverride: autoProcessOverride,
      maxEpisodes: maxEp !== null && !isNaN(maxEp) ? Math.max(10, Math.min(maxEp, 500)) : null,
    });
  };

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

  // Determine which bulk actions are valid for current selection
  const selectedEpisodes = episodes.filter(ep => selectedIds.has(ep.id));
  const allDiscovered = selectedEpisodes.length > 0 && selectedEpisodes.every(ep => ep.status === 'discovered');
  const allProcessed = selectedEpisodes.length > 0 && selectedEpisodes.every(ep =>
    ['completed', 'failed', 'permanently_failed'].includes(ep.status)
  );
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

  return (
    <div>
      <Link to="/" className="text-primary hover:underline mb-4 inline-block">
        Back to Dashboard
      </Link>

      <div className="bg-card rounded-lg border border-border p-6 mb-6">
        <div className="flex flex-col sm:flex-row gap-6">
          <div className="w-32 h-32 flex-shrink-0 mx-auto sm:mx-0">
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
              <span>Feed cap: {feed.maxEpisodes || 300}</span>
            </div>

            {/* Network / DAI Platform info */}
            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
              {isEditingNetwork ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <label className="text-muted-foreground text-sm w-16 flex-shrink-0">Network:</label>
                    <select
                      value={editNetworkOverride}
                      onChange={(e) => setEditNetworkOverride(e.target.value)}
                      className="flex-1 min-w-0 px-2 py-1 text-sm bg-secondary border border-border rounded"
                    >
                      <option value="">Auto-detect</option>
                      {networks?.map((network) => (
                        <option key={network.id} value={network.id}>
                          {network.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="text-muted-foreground text-sm w-16 flex-shrink-0">DAI:</label>
                    <input
                      type="text"
                      value={editDaiPlatform}
                      onChange={(e) => setEditDaiPlatform(e.target.value)}
                      placeholder="e.g., megaphone, acast"
                      className="flex-1 min-w-0 px-2 py-1 text-sm bg-secondary border border-border rounded"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="text-muted-foreground text-sm w-16 flex-shrink-0">Feed cap:</label>
                    <input
                      type="number"
                      value={editMaxEpisodes}
                      onChange={(e) => setEditMaxEpisodes(e.target.value)}
                      placeholder="300"
                      min={10}
                      max={500}
                      className="w-20 px-2 py-1 text-sm bg-secondary border border-border rounded"
                    />
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={saveNetworkEdit}
                      disabled={updateMutation.isPending}
                      className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50"
                    >
                      {updateMutation.isPending ? 'Saving...' : 'Save'}
                    </button>
                    <button
                      onClick={() => setIsEditingNetwork(false)}
                      className="px-2 py-1 text-xs bg-muted text-muted-foreground rounded hover:bg-accent"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-3 flex-wrap">
                  {feed.networkId && (
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                      feed.networkIdOverride
                        ? 'bg-orange-500/20 text-orange-600 dark:text-orange-400'
                        : 'bg-green-500/20 text-green-600 dark:text-green-400'
                    }`}>
                      {feed.networkIdOverride ? 'Override' : 'Detected'}: {feed.networkId}
                    </span>
                  )}
                  {feed.daiPlatform && (
                    <span className="px-2 py-0.5 bg-purple-500/20 text-purple-600 dark:text-purple-400 rounded text-xs font-medium">
                      DAI: {feed.daiPlatform}
                    </span>
                  )}
                  <button
                    onClick={startEditingNetwork}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    {feed.networkId || feed.daiPlatform ? 'Edit' : '+ Add Network'}
                  </button>
                </div>
              )}
            </div>

            {/* Podcast Settings - Always visible */}
            <div className="mt-4 space-y-3">

              {/* Auto-Process Control */}
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
                <span className="text-muted-foreground whitespace-nowrap">Auto-Process:</span>
                <div className="flex items-center gap-2 flex-wrap">
                  <select
                    value={
                      feed.autoProcessOverride === true ? 'enable' :
                      feed.autoProcessOverride === false ? 'disable' : 'global'
                    }
                    onChange={(e) => {
                      const value = e.target.value;
                      let autoProcessOverride: boolean | null = null;
                      if (value === 'enable') autoProcessOverride = true;
                      else if (value === 'disable') autoProcessOverride = false;
                      updateMutation.mutate({ autoProcessOverride: autoProcessOverride });
                    }}
                    disabled={updateMutation.isPending}
                    className="px-2 py-1.5 text-sm bg-secondary border border-border rounded flex-1 sm:flex-none min-w-0"
                  >
                    <option value="global">Global Default</option>
                    <option value="enable">Enabled</option>
                    <option value="disable">Disabled</option>
                  </select>
                  {feed.autoProcessOverride !== null && feed.autoProcessOverride !== undefined && (
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                      feed.autoProcessOverride
                        ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                        : 'bg-red-500/20 text-red-600 dark:text-red-400'
                    }`}>
                      {feed.autoProcessOverride ? 'Enabled' : 'Disabled'}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-6 pt-4 border-t border-border flex flex-wrap gap-4 items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="hidden sm:inline text-sm text-muted-foreground flex-shrink-0">Feed URL:</span>
            <code className="hidden sm:block text-sm bg-secondary px-2 py-1 rounded truncate min-w-0">
              {feed.feedUrl}
            </code>
            <button
              onClick={copyFeedUrl}
              className="px-4 py-2 sm:px-0 sm:py-0 sm:p-1.5 rounded flex items-center gap-2 bg-secondary sm:bg-transparent text-secondary-foreground sm:text-muted-foreground hover:bg-secondary/80 sm:hover:bg-accent hover:text-foreground transition-colors"
              title="Copy feed URL"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                />
              </svg>
              <span className="text-sm sm:hidden">Copy Feed URL</span>
            </button>
          </div>
          <div className="flex gap-2">
            {/* Reprocess Dropdown */}
            <div className="relative">
              <button
                onClick={() => setShowReprocessDropdown(!showReprocessDropdown)}
                disabled={reprocessAllMutation.isPending}
                className="px-4 py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors flex items-center gap-2"
                title="Reprocess all processed episodes"
              >
                {reprocessAllMutation.isPending ? 'Queuing...' : 'Reprocess All'}
                <svg className={`w-4 h-4 transition-transform ${showReprocessDropdown ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              {showReprocessDropdown && (
                <div className="absolute right-0 mt-1 w-56 bg-card border border-border rounded-lg shadow-lg z-10">
                  <button
                    onClick={() => {
                      setSelectedReprocessMode('reprocess');
                      setShowReprocessDropdown(false);
                      setShowReprocessConfirm(true);
                    }}
                    className="w-full px-4 py-2 text-left hover:bg-accent transition-colors rounded-t-lg"
                  >
                    <span className="block text-sm font-medium text-foreground">Patterns + AI</span>
                    <span className="block text-xs text-muted-foreground">Use learned patterns for faster detection</span>
                  </button>
                  <button
                    onClick={() => {
                      setSelectedReprocessMode('full');
                      setShowReprocessDropdown(false);
                      setShowReprocessConfirm(true);
                    }}
                    className="w-full px-4 py-2 text-left hover:bg-accent transition-colors rounded-b-lg border-t border-border"
                  >
                    <span className="block text-sm font-medium text-foreground">AI Only</span>
                    <span className="block text-xs text-muted-foreground">Fresh analysis without patterns</span>
                  </button>
                </div>
              )}
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
      </div>

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
          <div className="flex items-center gap-2 ml-auto">
            {allDiscovered && (
              <button
                onClick={() => bulkMutation.mutate({ action: 'process' })}
                disabled={bulkMutation.isPending}
                className="px-3 py-1.5 text-sm rounded bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
              >
                {bulkMutation.isPending ? 'Processing...' : 'Process'}
              </button>
            )}
            {allProcessed && (
              <>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess' })}
                  disabled={bulkMutation.isPending}
                  className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50"
                >
                  Reprocess
                </button>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess_full' })}
                  disabled={bulkMutation.isPending}
                  className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50"
                >
                  Full Reprocess
                </button>
                <button
                  onClick={() => setShowBulkDeleteConfirm(true)}
                  disabled={bulkMutation.isPending}
                  className="px-3 py-1.5 text-sm rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
                >
                  Delete
                </button>
              </>
            )}
            {!allDiscovered && !allProcessed && hasSelection && (
              <span className="text-xs text-muted-foreground">Mixed statuses - select episodes with the same status for bulk actions</span>
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
          selectedIds={selectedIds}
          onToggle={handleToggleSelect}
          onSelectAll={handleSelectAll}
        />
      )}

      {/* Pagination controls */}
      {totalPages > 1 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <button
              onClick={() => handlePageChange(page - 1)}
              disabled={page <= 1}
              className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50"
            >
              Prev
            </button>
            <span className="text-sm text-muted-foreground">
              Page {page} of {totalPages}
            </span>
            <button
              onClick={() => handlePageChange(page + 1)}
              disabled={page >= totalPages}
              className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50"
            >
              Next
            </button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Per page:</span>
            {[25, 50, 100, 500].map(size => (
              <button
                key={size}
                onClick={() => handlePageSizeChange(size)}
                className={`px-2 py-1 text-xs rounded ${
                  pageSize === size
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
                }`}
              >
                {size}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Reprocess All Confirmation Modal */}
      {showReprocessConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full">
            <div className="p-6">
              <h2 className="text-xl font-semibold text-foreground mb-4">
                Reprocess All Episodes
              </h2>
              <div className="mb-4 p-3 rounded-lg bg-accent/50">
                <p className="text-sm font-medium text-foreground">
                  Mode: {selectedReprocessMode === 'reprocess' ? 'Patterns + AI' : 'AI Only'}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {selectedReprocessMode === 'reprocess'
                    ? 'Uses learned patterns for faster ad detection'
                    : 'Fresh analysis without pattern database'}
                </p>
              </div>
              <p className="text-sm text-muted-foreground mb-4">
                This will queue all processed episodes for reprocessing. Existing processed audio files will be deleted and episodes will be re-transcribed and re-analyzed.
              </p>
              <p className="text-sm text-yellow-600 dark:text-yellow-400 mb-6">
                This operation cannot be undone. Episodes currently processing will be skipped.
              </p>
              <div className="flex gap-3 justify-end">
                <button
                  onClick={() => setShowReprocessConfirm(false)}
                  className="px-4 py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80"
                >
                  Cancel
                </button>
                <button
                  onClick={() => reprocessAllMutation.mutate(selectedReprocessMode)}
                  disabled={reprocessAllMutation.isPending}
                  className="px-4 py-2 rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
                >
                  {reprocessAllMutation.isPending ? 'Queuing...' : 'Reprocess All'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Reprocess Results Modal */}
      {reprocessResult && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full">
            <div className="p-6">
              <h2 className="text-xl font-semibold text-foreground mb-4">Reprocess Queued</h2>
              <p className="text-xs text-muted-foreground mb-4">
                Mode: {reprocessResult.mode === 'reprocess' ? 'Patterns + AI' : 'AI Only'}
              </p>
              <div className="grid grid-cols-2 gap-4 text-center mb-4">
                <div className="p-3 rounded-lg bg-green-500/10">
                  <p className="text-2xl font-bold text-green-600 dark:text-green-400">{reprocessResult.queued}</p>
                  <p className="text-xs text-muted-foreground">Queued</p>
                </div>
                <div className="p-3 rounded-lg bg-yellow-500/10">
                  <p className="text-2xl font-bold text-yellow-600 dark:text-yellow-400">{reprocessResult.skipped}</p>
                  <p className="text-xs text-muted-foreground">Skipped</p>
                </div>
              </div>
              {reprocessResult.queued > 0 && (
                <p className="text-sm text-muted-foreground mb-4">
                  {reprocessResult.queued} episodes have been queued for {reprocessResult.mode === 'reprocess' ? 'pattern-assisted' : 'full AI'} reprocessing. They will be processed in the background.
                </p>
              )}
              <button
                onClick={closeReprocessModal}
                className="w-full px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reprocess Error Modal */}
      {reprocessAllMutation.error && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full">
            <div className="p-6">
              <h2 className="text-xl font-semibold text-destructive mb-4">Reprocess Failed</h2>
              <p className="text-sm text-muted-foreground mb-4">
                {(reprocessAllMutation.error as Error).message}
              </p>
              <button
                onClick={closeReprocessModal}
                className="w-full px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Delete Confirmation Modal */}
      {showBulkDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full">
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
                  className="px-4 py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80"
                >
                  Cancel
                </button>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'delete' })}
                  disabled={bulkMutation.isPending}
                  className="px-4 py-2 rounded bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
                >
                  {bulkMutation.isPending ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Action Result Modal */}
      {bulkResult && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full">
            <div className="p-6">
              <h2 className="text-xl font-semibold text-foreground mb-4">Bulk Action Complete</h2>
              <div className="grid grid-cols-2 gap-4 text-center mb-4">
                <div className="p-3 rounded-lg bg-green-500/10">
                  <p className="text-2xl font-bold text-green-600 dark:text-green-400">{bulkResult.queued}</p>
                  <p className="text-xs text-muted-foreground">Actioned</p>
                </div>
                <div className="p-3 rounded-lg bg-yellow-500/10">
                  <p className="text-2xl font-bold text-yellow-600 dark:text-yellow-400">{bulkResult.skipped}</p>
                  <p className="text-xs text-muted-foreground">Skipped</p>
                </div>
              </div>
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
                className="w-full px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default FeedDetail;
