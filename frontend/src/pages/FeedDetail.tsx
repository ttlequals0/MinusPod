import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { Pencil, Trash2 } from 'lucide-react';
import CloseButton from '../components/CloseButton';
import { useParams, Link, useNavigate, useLocation } from 'react-router';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getFeed, feedsQueryOptions, getEpisodes, refreshFeed, updateFeed, reprocessAllEpisodes, ReprocessAllResult, bulkEpisodeAction, BulkAction, UpdateFeedPayload, deleteFeed, setEpisodesPassthrough } from '../api/feeds';
import type { EpisodeSelection } from '../api/feeds';
import type { BulkActionResult } from '../api/types';
import { getErrorMessage, jobStateOf } from '../api/client';
import { isActionBlocked } from '../utils/processingStage';
import { applyEpisodeJobState } from '../utils/jobStateCache';
import { PendingRecutsBar } from './patterns/PendingRecutsBar';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import { useUrlParams } from '../hooks/useUrlParams';
import { SEARCH_DEBOUNCE_MS } from '../hooks/useDebouncedValue';
import { sortFeeds, FeedSortBy, DASHBOARD_SORT_KEY, DEFAULT_FEED_SORT } from '../utils/feedSort';
import PrevNextLink from '../components/PrevNextLink';
import Artwork from '../components/Artwork';
import { feedArtworkSrc } from '../utils/artworkUrl';
import CopyButton from '../components/CopyButton';
import DropdownMenu from '../components/DropdownMenu';
import EpisodeList from '../components/EpisodeList';
import { SkeletonPageHeader, SkeletonRows } from '../components/Skeleton';
import { Pagination } from '../components/Pagination';
import FeedTypeBadge from '../components/FeedTypeBadge';
import PodpingBadge from '../components/PodpingBadge';
import { feedDisplayTitle, feedHasUpstream, deleteStopsProcessingMessage } from '../utils/feedTitle';
import FeedSettingsPanel from './feeds/FeedSettingsPanel';
import LocalFeedPanel from './feeds/LocalFeedPanel';
import YouTubeFeedPanel from './feeds/YouTubeFeedPanel';
import FeedStatsCards from './feeds/FeedStatsCards';
import PodcastAdDistributionPanel from './feeds/PodcastAdDistributionPanel';
import RecentsFeedPanel from './feeds/RecentsFeedPanel';
import CueTemplatesPanel from './feeds/CueTemplatesPanel';
import { formatStorage } from './settings/settingsUtils';
import { formatDateTime } from '../utils/format';
import RichText from '../components/RichText';
import { Modal, ConfirmModal, dialogFooterBtn } from '../components/Modal';
import { btnBase, btnPrimary, btnSecondary, btnGhost, btnDestructive } from '../components/buttonStyles';
import { focusRing, inputBase, selectBase } from '../components/fieldStyles';
import { badgeBase, tint } from '../components/badgeStyles';
import { pageTitle, cardTitle, sectionPad } from '../components/pageStyles';
import { iconTarget, iconVisual } from '../components/rowActionStyles';
import { reprocessMenuCopy, reprocessModeKey } from '../utils/reprocessMenu';

function reprocessModeLabel(mode: string, feedType?: string): string {
  if (mode === 'full') return 'Full reprocess';
  if (mode === 'llm') return 'Re-detect ads';
  return feedType === 'youtube' ? 'SponsorBlock and patterns' : 'Patterns + AI';
}

function reprocessModeDescription(mode: string, feedType?: string): string {
  if (feedType === 'youtube') {
    const copy = reprocessMenuCopy(feedType);
    return copy[reprocessModeKey(mode)].tooltip;
  }
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
      <div className="p-3 rounded-lg bg-success/10">
        <p className="text-2xl font-bold text-success">{queued}</p>
        <p className="text-xs text-muted-foreground">{queuedLabel}</p>
      </div>
      <div className="p-3 rounded-lg bg-warning/10">
        <p className="text-2xl font-bold text-warning">{skipped}</p>
        <p className="text-xs text-muted-foreground">Skipped</p>
      </div>
    </div>
  );
}

function reprocessModeVerb(mode: string, feedType?: string): string {
  if (feedType === 'youtube') return mode === 'reprocess' ? 'SponsorBlock and pattern' : 'pass 1 LLM';
  if (mode === 'full') return 'full AI';
  if (mode === 'llm') return 'transcript-reuse';
  return 'pattern-assisted';
}

const EPISODE_PARAM_DEFAULTS = {
  page: '1', size: '25', status: 'all', sort: 'published_at:desc', q: '',
};
const PAGE_SIZES = [25, 50, 100, 500];

type EpisodeParam = keyof typeof EPISODE_PARAM_DEFAULTS;

// Page, filter, sort and search live in the URL so Back from an episode restores them.
function useEpisodeListParams() {
  const { read, readPage, write } = useUrlParams(EPISODE_PARAM_DEFAULTS);
  const size = Number(read('size'));
  const [sortBy, sortDir] = read('sort').split(':');
  return {
    page: readPage('page'),
    pageSize: PAGE_SIZES.includes(size) ? size : Number(EPISODE_PARAM_DEFAULTS.size),
    statusFilter: read('status'),
    episodeSearch: read('q'),
    sortBy,
    sortDir,
    writeParams: write,
  };
}

// Bulk actions pair up per line on a phone.
const bulkActionBtn = `${btnBase} flex-1 sm:flex-none min-w-[8rem]`;

function FeedDetail() {
  const { slug } = useParams<{ slug: string }>();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [showReprocessConfirm, setShowReprocessConfirm] = useState(false);
  const [selectedReprocessMode, setSelectedReprocessMode] = useState<'reprocess' | 'full' | 'llm'>('reprocess');
  const [reprocessResult, setReprocessResult] = useState<ReprocessAllResult | null>(null);

  // Pagination state
  const { page, pageSize, statusFilter, episodeSearch, sortBy, sortDir, writeParams } = useEpisodeListParams();
  const [searchInput, setSearchInput] = useState(episodeSearch);
  const selectionScope = JSON.stringify([slug, statusFilter, episodeSearch.trim()]);

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [selectedScope, setSelectedScope] = useState(selectionScope);
  const [matchedSelection, setMatchedSelection] = useState<{
    scope: string; episodes: EpisodeSelection[]; truncated: boolean;
  } | null>(null);
  const [selectionRequest, setSelectionRequest] = useState<{ id: number; scope: string } | null>(null);
  const selectingMatches = selectionRequest?.scope === selectionScope;
  const selectionRequestRef = useRef(0);
  useEffect(() => {
    selectionRequestRef.current += 1;
    return () => { selectionRequestRef.current += 1; };
  }, [slug]);
  // Anchor row for shift-click range selection; reset wherever selection is.
  const [selectionAnchor, setSelectionAnchor] = useState<string | null>(null);
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  // Feed deletion requires an explicit confirmation.
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [bulkResult, setBulkResult] = useState<BulkActionResult | null>(null);
  const [passthroughResult, setPassthroughResult] = useState<
    { enabled: boolean; updated: number; queued: number } | null
  >(null);

  const [selectionFeedSlug, setSelectionFeedSlug] = useState(slug);
  if (selectionFeedSlug !== slug) {
    setSelectionFeedSlug(slug);
    setSelectedIds(new Set());
    setSelectedScope(selectionScope);
    setMatchedSelection(null);
    setSelectionRequest(null);
    setSelectionAnchor(null);
    setShowBulkDeleteConfirm(false);
    setSearchInput(episodeSearch);
  }

  // AddFeed's local-feed create flow passes a notice through router state
  // (e.g. an artwork upload failure or size warning) since it can't set
  // component state on a page it's about to unmount from. Surface it once
  // through the same toast, then drop it from history so back/refresh don't
  // replay it.
  useEffect(() => {
    const notice = (location.state as { notice?: string } | null)?.notice;
    if (notice) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActionError(notice);
      navigate(location.pathname, { replace: true, state: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

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
    queryKey: ['episodes', slug, page, pageSize, statusFilter, sortBy, sortDir, episodeSearch.trim()],
    queryFn: () => getEpisodes(slug!, {
      limit: pageSize,
      offset: (page - 1) * pageSize,
      status: statusFilter,
      sortBy,
      sortDir,
      search: episodeSearch.trim(),
    }),
    enabled: !!slug,
  });

  const episodes = episodesData?.episodes ?? [];
  const totalEpisodes = episodesData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalEpisodes / pageSize));

  const selectionEpisodes = useMemo(() => {
    const rows = new Map<string, EpisodeSelection>();
    if (matchedSelection?.scope === selectionScope) {
      for (const episode of matchedSelection.episodes) rows.set(episode.id, episode);
    }
    for (const episode of episodesData?.episodes ?? []) rows.set(episode.id, episode);
    return [...rows.values()];
  }, [matchedSelection, selectionScope, episodesData]);
  const selectableIds = useMemo(
    () => new Set(selectionEpisodes.filter(ep => !isActionBlocked(ep.jobState, false)).map(ep => ep.id)),
    [selectionEpisodes],
  );
  const effectiveSelectedIds = useMemo(
    () => new Set(selectedScope === selectionScope
      ? [...selectedIds].filter(id => selectableIds.has(id)) : []),
    [selectedIds, selectableIds, selectedScope, selectionScope],
  );

  const clearSelection = useCallback(() => {
    selectionRequestRef.current += 1;
    setSelectedIds(new Set());
    setSelectedScope(selectionScope);
    setMatchedSelection(null);
    setSelectionRequest(null);
    setSelectionAnchor(null);
    setShowBulkDeleteConfirm(false);
  }, [selectionScope]);

  const selectAllMatches = async () => {
    const requestId = ++selectionRequestRef.current;
    setSelectionRequest({ id: requestId, scope: selectionScope });
    setActionError(null);
    try {
      const result = await getEpisodes(slug!, {
        status: statusFilter, search: episodeSearch.trim(), selection: true,
      });
      if (requestId !== selectionRequestRef.current) return;
      const matches = result.selection ?? result.episodes;
      setMatchedSelection({ scope: selectionScope, episodes: matches, truncated: result.truncated ?? false });
      setSelectedScope(selectionScope);
      setSelectedIds(new Set(matches.filter(ep => !isActionBlocked(ep.jobState, false)).map(ep => ep.id)));
      setSelectionAnchor(null);
    } catch (error) {
      if (requestId === selectionRequestRef.current) {
        setActionError(getErrorMessage(error, 'Could not select matching episodes.'));
      }
    } finally {
      setSelectionRequest(current => current?.id === requestId ? null : current);
    }
  };

  const refreshMutation = useMutation({
    mutationFn: (opts?: { force?: boolean }) => refreshFeed(slug!, opts),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
    onError: (err) => setActionError(getErrorMessage(err, 'Could not refresh this feed.')),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteFeed(slug!),
    onMutate: () => setActionError(null),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      if (result?.pending) {
        setDeleteConfirm(false);
        setActionError(result.message);
        return;
      }
      navigate('/');
    },
    onError: (err) => {
      setActionError(getErrorMessage(err, 'Could not delete this feed.'));
    },
  });

  const handleDeleteFeed = () => {
    setActionError(null);
    setDeleteConfirm(true);
  };

  const updateMutation = useMutation({
    mutationFn: (data: UpdateFeedPayload) => updateFeed(slug!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
      setIsEditingTitle(false);
    },
    onError: (err) => setActionError(getErrorMessage(err, 'Could not save this feed.')),
  });

  const reprocessAllMutation = useMutation({
    mutationFn: (mode: 'reprocess' | 'full' | 'llm') => reprocessAllEpisodes(slug!, mode),
    onSuccess: (result) => {
      setReprocessResult(result);
      setShowReprocessConfirm(false);
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
    },
    onError: (err) => {
      setShowReprocessConfirm(false);
      setActionError(getErrorMessage(err, 'Could not start reprocessing.'));
    },
  });

  const bulkMutation = useMutation({
    // Process accepts only rows the server can queue; other actions preserve
    // the full manual selection.
    mutationFn: ({ action }: { action: BulkAction }) =>
      bulkEpisodeAction(
        slug!,
        action === 'process'
          ? selectionEpisodes
              .filter(ep => effectiveSelectedIds.has(ep.id)
                && (ep.status === 'discovered' || ep.status === 'pending')
                && !ep.titleSkipped && !ep.descriptionSkipped && !ep.durationSkipped)
              .map(ep => ep.id)
          : Array.from(effectiveSelectedIds),
        action),
    onSuccess: (result: BulkActionResult, _variables, context: { ids: string[] } | undefined) => {
      setBulkResult(result);
      const skippedIds = new Set(
        (result.skippedEpisodes ?? []).map((item) => item.episodeId));
      applyEpisodeJobState(
        queryClient, slug!, (context?.ids ?? []).filter(id => !skippedIds.has(id)),
        jobStateOf(result));
      clearSelection();
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
    onMutate: ({ action }: { action: BulkAction }): { ids: string[] } => ({
      ids: action === 'process'
        ? selectionEpisodes
            .filter(ep => effectiveSelectedIds.has(ep.id)
              && (ep.status === 'discovered' || ep.status === 'pending')
              && !ep.titleSkipped && !ep.descriptionSkipped && !ep.durationSkipped)
            .map(ep => ep.id)
        : Array.from(effectiveSelectedIds),
    }),
    onError: (err) => {
      setShowBulkDeleteConfirm(false);
      setActionError(getErrorMessage(err, 'Could not apply that action.'));
    },
  });

  const passthroughMutation = useMutation({
    mutationFn: ({ enabled }: { enabled: boolean }) =>
      setEpisodesPassthrough(slug!, Array.from(effectiveSelectedIds), enabled),
    onSuccess: (result, variables) => {
      setPassthroughResult({ enabled: variables.enabled, updated: result.updated, queued: result.queued });
      // The response reports one state for the batch, so it can only be
      // applied when every accepted episode shares it.
      const accepted = result.accepted ?? [];
      if (result.queued === 0 || result.queued === accepted.length) {
        applyEpisodeJobState(queryClient, slug!, accepted, jobStateOf(result));
      }
      clearSelection();
      queryClient.invalidateQueries({ queryKey: ['episodes', slug] });
      queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
    onError: (err) => setActionError(getErrorMessage(err, 'Could not update pass-through.')),
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

  const handleToggleSelect = (id: string, shiftKey: boolean) => {
    setSelectedScope(selectionScope);
    // Shift+click applies the anchor row's current state to every selectable
    // row between the anchor and it (inclusive), in current page order, skipping
    // blocked rows (standard range-select). A plain click toggles one row and
    // becomes the new anchor.
    if (shiftKey && selectionAnchor && selectionAnchor !== id) {
      const order = episodes.map(ep => ep.id);
      const a = order.indexOf(selectionAnchor);
      const b = order.indexOf(id);
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        const select = selectedIds.has(selectionAnchor);
        setSelectedIds(() => {
          const next = new Set(effectiveSelectedIds);
          for (let i = lo; i <= hi; i++) {
            const rid = order[i];
            if (!selectableIds.has(rid)) continue;
            if (select) next.add(rid); else next.delete(rid);
          }
          return next;
        });
        setSelectionAnchor(id);
        return;
      }
    }
    setSelectedIds(() => {
      const next = new Set(effectiveSelectedIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setSelectionAnchor(id);
  };

  const handleSelectAll = (checked: boolean) => {
    clearSelection();
    if (checked) {
      setSelectedIds(new Set(episodes.filter(ep => !isActionBlocked(ep.jobState, false)).map(ep => ep.id)));
    }
  };

  // Any filter change starts over on page 1 and drops the selection.
  const changeFilter = useCallback((changes: Partial<Record<EpisodeParam, string>>) => {
    clearSelection();
    writeParams({ ...changes, page: '1' });
  }, [clearSelection, writeParams]);

  // The typed text reaches the URL (and the query) once typing pauses.
  useEffect(() => {
    if (searchInput === episodeSearch) return;
    const timer = setTimeout(() => changeFilter({ q: searchInput }), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, episodeSearch, changeFilter]);

  const handlePageChange = (newPage: number) => {
    writeParams({ page: String(newPage) });
    if (matchedSelection?.scope !== selectionScope) clearSelection();
  };

  // Bulk-action eligibility: count per-action so a mixed selection still
  // surfaces actionable buttons (backend skips ineligible rows).
  const selectedEpisodes = selectionEpisodes.filter(ep => effectiveSelectedIds.has(ep.id));
  const discoveredCount = selectedEpisodes.filter(ep => ep.status === 'discovered' && !ep.titleSkipped && !ep.descriptionSkipped && !ep.durationSkipped).length;
  const pendingCount = selectedEpisodes.filter(ep => ep.status === 'pending' && !ep.titleSkipped && !ep.descriptionSkipped && !ep.durationSkipped).length;
  const processedCount = selectedEpisodes.filter(ep =>
    ['completed', 'failed', 'permanently_failed', 'deferred'].includes(ep.status)
  ).length;
  const hasSelection = effectiveSelectedIds.size > 0;
  const selectionOverLimit = effectiveSelectedIds.size > 500;
  const selectionTruncated = matchedSelection?.scope === selectionScope && matchedSelection.truncated;
  // Keyed on jobState, not status: the run buttons key on jobState too, so the
  // note can never claim a selection is busy while those buttons stay live.
  const runningSelectedCount = selectionEpisodes.filter(
    (ep) => selectedIds.has(ep.id) && isActionBlocked(ep.jobState, false),
  ).length;
  const selectionNote = selectionTruncated
    ? 'More than 500 episodes matched. Narrow the search or select 500 or fewer.'
    : selectionOverLimit
    ? 'Select 500 episodes or fewer.'
    : runningSelectedCount > 0
    ? `Skipping ${runningSelectedCount} already running.`
    : discoveredCount + pendingCount + processedCount > 0
      ? null
      : 'No run actions apply to this selection.';
  const isRecents = feed?.feedType === 'recents';
  const stopsProcessingMessage = deleteStopsProcessingMessage(feed?.statusCounts?.processing ?? 0);

  if (feedLoading) {
    return (
      <div>
        <SkeletonPageHeader />
        <SkeletonRows count={6} />
      </div>
    );
  }

  if (feedError || !feed) {
    return (
      <div className="text-center py-12">
        <p className="text-destructive">Could not load this feed.</p>
        <Link to="/" className={`text-primary hover:underline mt-2 inline-block ${focusRing}`}>
          Back to feeds
        </Link>
      </div>
    );
  }

  // Hover feedback only when the artwork is actually a link (#521).
  const feedArtwork = (
    <Artwork
      src={feedArtworkSrc(slug!, feed.artworkUrl)}
      alt={feed.title}
      className={`w-full h-full object-cover rounded-lg${
        feed.websiteUrl ? ' hover:opacity-90 transition-opacity' : ''}`}
    />
  );

  const copy = reprocessMenuCopy(feed.feedType);

  return (
    <div>
      <div className="flex items-center justify-between gap-3 mb-4">
        <Link to="/" className={`text-primary hover:underline inline-block ${focusRing}`}>
          Back to feeds
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

      <div className={`bg-card rounded-lg border border-border p-4 sm:${sectionPad} mb-6`}>
        <div className="flex flex-col sm:flex-row gap-6">
          <div className="w-32 h-32 shrink-0 mx-auto sm:mx-0">
            {feed.websiteUrl ? (
              <a
                href={feed.websiteUrl}
                target="_blank"
                rel="noopener noreferrer"
                title={`Open the ${feed.title} website`}
                className={`block w-full h-full rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary ${focusRing}`}
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
                    className={`${inputBase} flex-1 min-w-0`}
                    aria-label="Feed title"
                  />
                  <button
                    onClick={saveTitleEdit}
                    disabled={updateMutation.isPending}
                    className={`${btnBase} ${btnPrimary}`}
                  >
                    {updateMutation.isPending ? 'Saving...' : 'Save'}
                  </button>
                  <button
                    onClick={() => setIsEditingTitle(false)}
                    className={`${btnBase} ${btnSecondary}`}
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
                <h1 className={pageTitle}>
                  {feedDisplayTitle(feed)}
                </h1>
                <FeedTypeBadge feedType={feed.feedType} className="mt-1.5" />
                {feed.titleOverride && (
                  <span className={`${badgeBase} mt-1.5 shrink-0 ${tint.blue}`}>
                    Custom
                  </span>
                )}
                <button
                  onClick={startEditingTitle}
                  aria-label="Edit feed title"
                  title="Edit feed title"
                  className={`${iconTarget} mt-1.5`}
                >
                  <span className={`${iconVisual} ${btnGhost}`}>
                    <Pencil aria-hidden="true" className="w-4 h-4" />
                  </span>
                </button>
              </div>
            )}
            {feed.description && (
              <RichText html={feed.description} className="text-muted-foreground mt-2 block wrap-break-word" />
            )}
            <div className="mt-4 flex flex-wrap gap-4 text-sm text-muted-foreground">
              <span>{feed.episodeCount} episodes</span>
              {feed.lastRefreshed && (
                <span>Updated {formatDateTime(feed.lastRefreshed)}</span>
              )}
              <PodpingBadge
                coverage={feed.podpingCoverage}
                lastPodpingAt={feed.lastPodpingAt}
              />
              {feedHasUpstream(feed) && feed.lastRefreshError && (
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

        <div className="mt-6 pt-4 border-t border-border grid grid-cols-2 min-[360px]:flex gap-2 sm:gap-4 items-center sm:flex-wrap sm:justify-between">
          <div className="flex items-center gap-2 min-w-0 shrink-0 sm:shrink">
            <span className="hidden sm:inline text-sm text-muted-foreground shrink-0">Feed URL</span>
            <code className="hidden sm:block text-sm bg-secondary px-2 py-1 rounded truncate min-w-0">
              {feed.feedUrl}
            </code>
            {/* Not hideLabelOnMobile: its baseClass adds sm:px-2 and h-8, which
                beat this call site's desktop padding because Tailwind orders
                same-utility conflicts by scale, not by source order. */}
            <CopyButton
              text={feed.feedUrl}
              label="Copy feed URL"
              className={`max-[359px]:w-full px-2 py-2 sm:px-0 sm:py-0 sm:p-1.5 gap-2 ${btnSecondary} sm:bg-transparent sm:text-muted-foreground sm:hover:bg-accent`}
              copiedClassName="text-success bg-success/10 sm:bg-transparent"
              labelClassName="hidden sm:inline text-sm"
            />
          </div>
          {/* Below sm: one row (copy first), a 2x2 grid under 360 px; from sm it wraps so nowrap buttons never overflow. */}
          <div className="contents min-[360px]:flex min-[360px]:flex-1 min-w-0 gap-2 [&>div]:flex-1 sm:flex-none sm:flex-wrap sm:justify-end sm:[&>div]:flex-none">
            {!isRecents && (
            <DropdownMenu
              triggerLabel={
                <><span className="sm:hidden">Reprocess</span><span className="hidden sm:inline">{reprocessAllMutation.isPending ? 'Queuing...' : 'Reprocess all'}</span></>
              }
              triggerClassName={`${btnBase} ${btnSecondary} w-full sm:w-auto`}
              disabled={reprocessAllMutation.isPending}
              title="Reprocess all processed episodes"
              align="left"
              items={[
                {
                  title: reprocessModeLabel('reprocess', feed.feedType),
                  subtitle: copy.reprocess.subtitle,
                  onClick: () => {
                    setSelectedReprocessMode('reprocess');
                    setShowReprocessConfirm(true);
                  },
                },
                {
                  title: 'Full reprocess',
                  subtitle: copy.full.subtitle,
                  onClick: () => {
                    setSelectedReprocessMode('full');
                    setShowReprocessConfirm(true);
                  },
                },
                {
                  title: 'Re-detect ads',
                  subtitle: copy.redetect.subtitle,
                  onClick: () => {
                    setSelectedReprocessMode('llm');
                    setShowReprocessConfirm(true);
                  },
                },
              ]}
            />
            )}
            {feedHasUpstream(feed) && (
              <DropdownMenu
                triggerLabel={
                  <><span className="sm:hidden">Refresh</span><span className="hidden sm:inline">{refreshMutation.isPending ? 'Refreshing...' : 'Refresh feed'}</span></>
                }
                triggerClassName={`${btnBase} ${btnPrimary} w-full sm:w-auto`}
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
            )}
            {/* Same button and confirm flow as the dashboard's feed cards. */}
            <button
              onClick={handleDeleteFeed}
              disabled={deleteMutation.isPending}
              className={`${btnBase} ${btnDestructive} shrink-0 max-[359px]:w-full`}
              title="Delete feed"
              aria-label="Delete feed"
            >
              <Trash2 className="w-4 h-4 sm:hidden" aria-hidden="true" />
              <span className="hidden sm:inline">Delete</span>
            </button>
          </div>
        </div>
      </div>

      {refreshMutation.isPending && <p role="status" className="mb-4 text-sm text-muted-foreground">Refreshing feed...</p>}
      {reprocessAllMutation.isPending && <p role="status" className="mb-4 text-sm text-muted-foreground">Queuing episodes...</p>}

      {slug && !isRecents && <FeedStatsCards feed={feed} slug={slug} />}

      {slug && isRecents && <RecentsFeedPanel feed={feed} slug={slug} />}

      {slug && !isRecents && <FeedSettingsPanel key={slug} feed={feed} slug={slug} />}

      {slug && feed.feedType === 'local' && <LocalFeedPanel feed={feed} slug={slug} />}
      {slug && feed.feedType === 'youtube' && <YouTubeFeedPanel key={`youtube-${slug}`} feed={feed} slug={slug} />}

      {slug && !isRecents && <PodcastAdDistributionPanel slug={slug} />}

      {slug && !isRecents && <CueTemplatesPanel slug={slug} />}

      {/* Decisions made on this feed's episodes, not yet in the audio. */}
      {slug && !isRecents && <PendingRecutsBar slug={slug} />}

      {/* Episodes header with status filter */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className={cardTitle}>
          Episodes {totalEpisodes > 0 && <span className="text-muted-foreground font-normal text-sm">({totalEpisodes})</span>}
        </h2>
        {/* The pair shares one row and shrinks to fit rather than stacking:
            wrapping put each select on its own line at ordinary phone widths. */}
        {!isRecents && (
        <div className="flex items-center gap-2 min-w-0 w-full sm:w-auto">
          <select
            value={statusFilter}
            onChange={(e) => changeFilter({ status: e.target.value })}
            className={`flex-1 min-w-0 sm:flex-none ${selectBase}`}
          >
            <option value="all">All statuses</option>
            <option value="discovered">Discovered</option>
            <option value="pending">Pending</option>
            <option value="processing">Processing</option>
            <option value="processed">Completed</option>
            <option value="failed">Failed</option>
            <option value="permanently_failed">Permanently failed</option>
            <option value="deferred">Queued (offline)</option>
          </select>
          <select
            value={`${sortBy}:${sortDir}`}
            onChange={(e) => changeFilter({ sort: e.target.value })}
            className={`flex-1 min-w-0 sm:flex-none ${selectBase}`}
          >
            <option value="published_at:desc">Newest first</option>
            <option value="published_at:asc">Oldest first</option>
            <option value="episode_number:desc">Episode # (high to low)</option>
            <option value="episode_number:asc">Episode # (low to high)</option>
          </select>
        </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        <input
          type="search"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Search episode titles..."
          aria-label="Search episode titles"
          className={`${inputBase} flex-1 min-w-0`}
        />
        {!isRecents && totalEpisodes > 0 && (
          <button
            type="button"
            onClick={selectAllMatches}
            disabled={episodesLoading || selectingMatches || bulkMutation.isPending || passthroughMutation.isPending}
            className={`${btnBase} ${btnSecondary}`}
          >
            {selectingMatches ? 'Selecting...' : `Select all ${totalEpisodes} matches`}
          </button>
        )}
      </div>

      {/* Bulk action toolbar */}
      {hasSelection && (
        <div className="mb-4 p-3 bg-secondary/50 rounded-lg border border-border flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">{effectiveSelectedIds.size} selected</span>
          <div className="flex flex-wrap items-center gap-2 ml-auto">
            {discoveredCount + pendingCount > 0 && (
              <button
                onClick={() => bulkMutation.mutate({ action: 'process' })}
                disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
                className={`${bulkActionBtn} ${btnPrimary}`}
              >
                {bulkMutation.isPending ? 'Processing...' : `Process now (${discoveredCount + pendingCount})`}
              </button>
            )}
            {processedCount > 0 && (
              <>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess' })}
                  disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
                  className={`${bulkActionBtn} ${btnSecondary}`}
                >
                  Reprocess ({processedCount})
                </button>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess_full' })}
                  disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
                  className={`${bulkActionBtn} ${btnSecondary}`}
                >
                  Full reprocess ({processedCount})
                </button>
                <button
                  onClick={() => bulkMutation.mutate({ action: 'reprocess_llm' })}
                  disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
                  className={`${bulkActionBtn} ${btnSecondary}`}
                  title="Re-detect ads using existing transcripts (skips re-transcription)"
                >
                  Re-detect ads ({processedCount})
                </button>
              </>
            )}
            {/* Pass-through applies regardless of episode status; only actively
                processing rows are excluded from selection in the first place. */}
            <button
              onClick={() => passthroughMutation.mutate({ enabled: true })}
              disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
              title="Serve these episodes unmodified, with no ad processing"
              className={`${bulkActionBtn} ${btnSecondary}`}
            >
              {passthroughMutation.isPending && passthroughMutation.variables?.enabled
                ? 'Setting...' : `Set pass-through (${effectiveSelectedIds.size})`}
            </button>
            <button
              onClick={() => passthroughMutation.mutate({ enabled: false })}
              disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
              title="Resume normal ad processing for these episodes"
              className={`${bulkActionBtn} ${btnSecondary}`}
            >
              {passthroughMutation.isPending && passthroughMutation.variables?.enabled === false
                ? 'Clearing...' : `Clear pass-through (${effectiveSelectedIds.size})`}
            </button>
            {processedCount > 0 && (
              <button
                onClick={() => setShowBulkDeleteConfirm(true)}
                disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
                className={`${bulkActionBtn} ${btnDestructive}`}
              >
                Delete ({processedCount})
              </button>
            )}
            {selectionNote && (
              <span className="text-xs text-muted-foreground">{selectionNote}</span>
            )}
            <button
              onClick={clearSelection}
              className={`${btnBase} ${btnGhost}`}
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {episodesLoading ? (
        <SkeletonRows count={6} />
      ) : (
        <EpisodeList
          episodes={episodes}
          feedSlug={slug!}
          feedArtworkUrl={feed.artworkUrl}
          selectedIds={isRecents ? undefined : effectiveSelectedIds}
          onToggle={isRecents ? undefined : handleToggleSelect}
          onSelectAll={isRecents ? undefined : handleSelectAll}
        />
      )}

      {/* Pagination controls (shared Pagination renders nothing at 1 page) */}
      <Pagination page={page} totalPages={totalPages} total={totalEpisodes} onPage={handlePageChange} />
      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-end gap-2">
          <span className="text-sm text-muted-foreground">Per page</span>
          {PAGE_SIZES.map(size => (
            <button
              key={size}
              onClick={() => changeFilter({ size: String(size) })}
              className={`${btnBase} ${pageSize === size ? btnPrimary : btnSecondary}`}
              aria-pressed={pageSize === size}
            >
              {size}
            </button>
          ))}
        </div>
      )}

      {showReprocessConfirm && (
        <Modal
          ariaLabel="Reprocess all episodes"
          onClose={() => setShowReprocessConfirm(false)}
          panelClassName="max-w-md w-full"
          header={(
            <h2 className={cardTitle}>Reprocess all episodes?</h2>
          )}
          footer={(
            <>
              <button
                onClick={() => setShowReprocessConfirm(false)}
                className={`${dialogFooterBtn} ${btnSecondary}`}
              >
                Cancel
              </button>
              <button
                onClick={() => reprocessAllMutation.mutate(selectedReprocessMode)}
                disabled={reprocessAllMutation.isPending}
                className={`${dialogFooterBtn} ${btnDestructive}`}
              >
                {reprocessAllMutation.isPending ? 'Queuing...' : 'Reprocess all episodes'}
              </button>
            </>
          )}
        >
          <div className="p-4">
            <div className="mb-4 p-3 rounded-lg bg-accent/50">
              <p className="text-sm font-medium text-foreground">
                Mode: {reprocessModeLabel(selectedReprocessMode, feed.feedType)}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {reprocessModeDescription(selectedReprocessMode, feed.feedType)}
              </p>
            </div>
            <p className="text-sm text-muted-foreground mb-4">
              {selectedReprocessMode === 'llm'
                ? 'This will queue all processed episodes that have a saved transcript. The transcript is reused (no re-transcription); audio is re-analyzed and re-cut. Episodes without a transcript are skipped.'
                : feed.feedType === 'youtube'
                ? 'This will queue all processed episodes. SponsorBlock marks and captions are refreshed, and audio is re-cut using this mode.'
                : 'This will queue all processed episodes for reprocessing. Existing processed audio files will be deleted and episodes will be re-transcribed and re-analyzed.'}
            </p>
            <p className="text-sm text-warning">
              This operation cannot be undone. Episodes currently processing will be skipped.
            </p>
          </div>
        </Modal>
      )}

      {reprocessResult && (
        <Modal
          ariaLabel="Reprocess queued"
          onClose={closeReprocessModal}
          panelClassName="max-w-md w-full"
          header={(
            <h2 className={cardTitle}>Reprocess queued</h2>
          )}
          footer={(
            <button onClick={closeReprocessModal} className={`${dialogFooterBtn} ${btnPrimary}`}>
              Close
            </button>
          )}
        >
          <div className="p-4">
            <p className="text-xs text-muted-foreground mb-4">
              Mode: {reprocessModeLabel(reprocessResult.mode, feed.feedType)}
            </p>
            <ResultStatsGrid queued={reprocessResult.queued} skipped={reprocessResult.skipped} queuedLabel="Queued" />
            {reprocessResult.queued > 0 && (
              <p className="text-sm text-muted-foreground">
                {reprocessResult.queued} episodes have been queued for {reprocessModeVerb(reprocessResult.mode, feed.feedType)} reprocessing. They will be processed in the background.
              </p>
            )}
          </div>
        </Modal>
      )}

      {reprocessAllMutation.error && (
        <Modal
          ariaLabel="Could not reprocess episodes"
          onClose={closeReprocessModal}
          panelClassName="max-w-md w-full"
          header={(
            <h2 className={cardTitle}>Could not reprocess episodes</h2>
          )}
          footer={(
            <button onClick={closeReprocessModal} className={`${dialogFooterBtn} ${btnPrimary}`}>
              Close
            </button>
          )}
        >
          <p className="p-4 text-sm text-muted-foreground">
            {(reprocessAllMutation.error as Error).message}
          </p>
        </Modal>
      )}

      {showBulkDeleteConfirm && (
        <Modal
          ariaLabel="Delete selected episodes"
          onClose={() => setShowBulkDeleteConfirm(false)}
          panelClassName="max-w-md w-full"
          header={(
            <h2 className={cardTitle}>
              Delete {effectiveSelectedIds.size} episode{effectiveSelectedIds.size > 1 ? 's' : ''}?
            </h2>
          )}
          footer={(
            <>
              <button
                onClick={() => setShowBulkDeleteConfirm(false)}
                className={`${dialogFooterBtn} ${btnSecondary}`}
              >
                Cancel
              </button>
              <button
                onClick={() => bulkMutation.mutate({ action: 'delete' })}
                disabled={bulkMutation.isPending || passthroughMutation.isPending || selectionOverLimit}
                className={`${dialogFooterBtn} ${btnDestructive}`}
              >
                {bulkMutation.isPending
                  ? 'Deleting...'
                  : `Delete ${effectiveSelectedIds.size} episode${effectiveSelectedIds.size > 1 ? 's' : ''}`}
              </button>
            </>
          )}
        >
          <p className="p-4 text-sm text-muted-foreground">
            This will delete processed audio files and reset selected episodes to discovered status. Episode records and processing history are preserved.
          </p>
        </Modal>
      )}

      {bulkResult && (
        <Modal
          ariaLabel="Bulk action complete"
          onClose={() => setBulkResult(null)}
          panelClassName="max-w-md w-full"
          header={(
            <h2 className={cardTitle}>Bulk action complete</h2>
          )}
          footer={(
            <button onClick={() => setBulkResult(null)} className={`${dialogFooterBtn} ${btnPrimary}`}>
              Close
            </button>
          )}
        >
          <div className="p-4">
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
            {bulkResult.skippedEpisodes?.some(item => item.reason === 'Already queued' || item.reason === 'Already processing') && (
              <p className="text-sm text-muted-foreground">
                Episodes already queued or processing were skipped.
              </p>
            )}
          </div>
        </Modal>
      )}

      {passthroughResult && (
        <Modal
          ariaLabel="Pass-through result"
          onClose={() => setPassthroughResult(null)}
          panelClassName="max-w-md w-full"
          header={(
            <h2 className={cardTitle}>
              {passthroughResult.enabled ? 'Pass-through set' : 'Pass-through cleared'}
            </h2>
          )}
          footer={(
            <button onClick={() => setPassthroughResult(null)} className={`${dialogFooterBtn} ${btnPrimary}`}>
              Close
            </button>
          )}
        >
          <p className="p-4 text-sm text-muted-foreground">
            {passthroughResult.updated} episode{passthroughResult.updated === 1 ? '' : 's'} updated.
            {passthroughResult.enabled && passthroughResult.queued > 0
              ? ` ${passthroughResult.queued} queued to run pass-through.` : ''}
          </p>
        </Modal>
      )}
      {/* Same bottom-right confirm/error toasts the dashboard uses. */}
      {(deleteConfirm || actionError) && (
        <div className="fixed bottom-4 right-[max(1rem,env(safe-area-inset-right))] flex flex-col items-end gap-2">
          {actionError && !deleteConfirm && (
            <div className="max-w-sm bg-destructive/10 border border-destructive text-destructive rounded-lg p-4 shadow-lg text-sm flex items-start gap-3">
              <span className="flex-1">{actionError}</span>
              <CloseButton onClick={() => setActionError(null)} label="Dismiss error" className="shrink-0" />
            </div>
          )}
          {deleteConfirm && (
            <ConfirmModal title={`Delete ${feed?.title || slug}?`} confirmLabel="Delete feed" pending={deleteMutation.isPending}
              onCancel={() => { setDeleteConfirm(false); setActionError(null); }} onConfirm={() => deleteMutation.mutate()}>
              <p>This removes the feed and its downloaded files.</p>
              {actionError && <p role="alert" className="text-destructive">{actionError}</p>}
              {stopsProcessingMessage && (
                <p className="text-sm text-warning mt-1">{stopsProcessingMessage}</p>
              )}
              {feed?.feedType === 'local' && (
                <p className="text-sm text-warning mt-1">
                  This is a local feed: the imported originals are the only copy and will be deleted.
                </p>
              )}
              {isRecents && (
                <p className="text-sm text-muted-foreground mt-1">
                  Only the combined feed is removed; the source feeds and episodes stay.
                </p>
              )}
            </ConfirmModal>
          )}
        </div>
      )}

    </div>
  );
}

export default FeedDetail;
