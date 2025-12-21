import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getFeed, getEpisodes, refreshFeed, updateFeed, getNetworks, reprocessAllEpisodes, ReprocessAllResult } from '../api/feeds';
import EpisodeList from '../components/EpisodeList';
import LoadingSpinner from '../components/LoadingSpinner';

function FeedDetail() {
  const { slug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const [isEditingNetwork, setIsEditingNetwork] = useState(false);
  const [showReprocessConfirm, setShowReprocessConfirm] = useState(false);
  const [reprocessResult, setReprocessResult] = useState<ReprocessAllResult | null>(null);
  const [editNetworkOverride, setEditNetworkOverride] = useState<string>('');
  const [editDaiPlatform, setEditDaiPlatform] = useState('');
  const [editAudioAnalysisOverride, setEditAudioAnalysisOverride] = useState<string>('global');
  const [editAutoProcessOverride, setEditAutoProcessOverride] = useState<string>('global');

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
    mutationFn: (data: { networkIdOverride?: string | null; daiPlatform?: string; audioAnalysisOverride?: boolean | null; autoProcessOverride?: boolean | null }) => updateFeed(slug!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      setIsEditingNetwork(false);
    },
  });

  const reprocessAllMutation = useMutation({
    mutationFn: () => reprocessAllEpisodes(slug!),
    onSuccess: (result) => {
      setReprocessResult(result);
      setShowReprocessConfirm(false);
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
  });

  const closeReprocessModal = () => {
    setShowReprocessConfirm(false);
    setReprocessResult(null);
    reprocessAllMutation.reset();
  };

  const startEditingNetwork = () => {
    // Use networkIdOverride if set, otherwise empty for auto-detect
    setEditNetworkOverride(feed?.networkIdOverride || '');
    setEditDaiPlatform(feed?.daiPlatform || '');
    // Initialize audio analysis override: 'global' (null), 'enable' (true), 'disable' (false)
    if (feed?.audioAnalysisOverride === true) {
      setEditAudioAnalysisOverride('enable');
    } else if (feed?.audioAnalysisOverride === false) {
      setEditAudioAnalysisOverride('disable');
    } else {
      setEditAudioAnalysisOverride('global');
    }
    // Initialize auto-process override
    if (feed?.autoProcessOverride === true) {
      setEditAutoProcessOverride('enable');
    } else if (feed?.autoProcessOverride === false) {
      setEditAutoProcessOverride('disable');
    } else {
      setEditAutoProcessOverride('global');
    }
    setIsEditingNetwork(true);
  };

  const saveNetworkEdit = () => {
    // Convert audio analysis override value
    let audioOverride: boolean | null = null;
    if (editAudioAnalysisOverride === 'enable') {
      audioOverride = true;
    } else if (editAudioAnalysisOverride === 'disable') {
      audioOverride = false;
    }

    // Convert auto-process override value
    let autoProcessOverride: boolean | null = null;
    if (editAutoProcessOverride === 'enable') {
      autoProcessOverride = true;
    } else if (editAutoProcessOverride === 'disable') {
      autoProcessOverride = false;
    }

    updateMutation.mutate({
      // Send null to clear override, or the selected value
      networkIdOverride: editNetworkOverride || null,
      daiPlatform: editDaiPlatform || undefined,
      audioAnalysisOverride: audioOverride,
      autoProcessOverride: autoProcessOverride,
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

            {/* Network / DAI Platform info */}
            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
              {isEditingNetwork ? (
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1">
                    <label className="text-muted-foreground">Network:</label>
                    <select
                      value={editNetworkOverride}
                      onChange={(e) => setEditNetworkOverride(e.target.value)}
                      className="px-2 py-1 text-sm bg-secondary border border-border rounded"
                    >
                      <option value="">Auto-detect</option>
                      {networks?.map((network) => (
                        <option key={network.id} value={network.id}>
                          {network.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex items-center gap-1">
                    <label className="text-muted-foreground">DAI:</label>
                    <input
                      type="text"
                      value={editDaiPlatform}
                      onChange={(e) => setEditDaiPlatform(e.target.value)}
                      placeholder="e.g., megaphone, acast"
                      className="px-2 py-1 text-sm bg-secondary border border-border rounded w-32"
                    />
                  </div>
                  <div className="flex items-center gap-1">
                    <label className="text-muted-foreground">Audio Analysis:</label>
                    <select
                      value={editAudioAnalysisOverride}
                      onChange={(e) => setEditAudioAnalysisOverride(e.target.value)}
                      className="px-2 py-1 text-sm bg-secondary border border-border rounded"
                    >
                      <option value="global">Use Global</option>
                      <option value="enable">Enable</option>
                      <option value="disable">Disable</option>
                    </select>
                  </div>
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
              ) : (
                <div className="flex items-center gap-3">
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
              {/* Audio Analysis Control */}
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 text-sm">
                <span className="text-muted-foreground whitespace-nowrap">Audio Analysis:</span>
                <div className="flex items-center gap-2 flex-wrap">
                  <select
                    value={
                      feed.audioAnalysisOverride === true ? 'enable' :
                      feed.audioAnalysisOverride === false ? 'disable' : 'global'
                    }
                    onChange={(e) => {
                      const value = e.target.value;
                      let audioOverride: boolean | null = null;
                      if (value === 'enable') audioOverride = true;
                      else if (value === 'disable') audioOverride = false;
                      updateMutation.mutate({ audioAnalysisOverride: audioOverride });
                    }}
                    disabled={updateMutation.isPending}
                    className="px-2 py-1.5 text-sm bg-secondary border border-border rounded flex-1 sm:flex-none min-w-0"
                  >
                    <option value="global">Use Global Setting</option>
                    <option value="enable">Always Enable</option>
                    <option value="disable">Always Disable</option>
                  </select>
                  {feed.audioAnalysisOverride !== null && feed.audioAnalysisOverride !== undefined && (
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                      feed.audioAnalysisOverride
                        ? 'bg-green-500/20 text-green-600 dark:text-green-400'
                        : 'bg-red-500/20 text-red-600 dark:text-red-400'
                    }`}>
                      {feed.audioAnalysisOverride ? 'Enabled' : 'Disabled'}
                    </span>
                  )}
                </div>
              </div>

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
                    <option value="global">Use Global Setting</option>
                    <option value="enable">Always Enable</option>
                    <option value="disable">Always Disable</option>
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
          <div className="flex gap-2">
            <button
              onClick={() => setShowReprocessConfirm(true)}
              disabled={reprocessAllMutation.isPending}
              className="px-4 py-2 rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
              title="Reprocess all processed episodes"
            >
              {reprocessAllMutation.isPending ? 'Queuing...' : 'Reprocess All'}
            </button>
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

      <h2 className="text-xl font-semibold text-foreground mb-4">Episodes</h2>
      {episodesLoading ? (
        <LoadingSpinner />
      ) : (
        <EpisodeList episodes={episodes || []} feedSlug={slug!} />
      )}

      {/* Reprocess All Confirmation Modal */}
      {showReprocessConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-card border border-border rounded-lg shadow-xl max-w-md w-full">
            <div className="p-6">
              <h2 className="text-xl font-semibold text-foreground mb-4">Reprocess All Episodes</h2>
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
                  onClick={() => reprocessAllMutation.mutate()}
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
                  {reprocessResult.queued} episodes have been queued for reprocessing. They will be processed in the background.
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
    </div>
  );
}

export default FeedDetail;
