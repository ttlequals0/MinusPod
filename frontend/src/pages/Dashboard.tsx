import CloseButton from '../components/CloseButton';
import { useState, useRef } from 'react';
import DashboardControlsMenu from '../components/DashboardControlsMenu';
import SegmentedToggle from '../components/SegmentedToggle';
import { keepPreviousData, useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, Link } from 'react-router';
import { Download, PanelsTopLeft, Plus, RefreshCw, Search } from 'lucide-react';
import { feedsQueryOptionsFor, refreshFeed, refreshAllFeeds, deleteFeed } from '../api/feeds';
import { FeedOpmlExportDialog } from '../components/FeedOpmlExportDialog';
import DropdownMenu from '../components/DropdownMenu';
import FeedCard from '../components/FeedCard';
import FeedListItem from '../components/FeedListItem';
import DashboardEpisodeGroups, {
  DEFAULT_EPISODES_PER_PODCAST,
  MIN_EPISODES_PER_PODCAST,
  MAX_EPISODES_PER_PODCAST,
  clampEpisodesPerPodcast,
} from '../components/DashboardEpisodeGroups';
import { Skeleton, SkeletonRows, SkeletonStatCards } from '../components/Skeleton';
import SearchResults from '../components/SearchResults';
import type { SearchResultRow } from '../components/SearchResults';
import { useUnifiedSearch } from '../hooks/useUnifiedSearch';
import { useLocalStorageState } from '../hooks/useLocalStorageState';
import { useOutsideClick } from '../hooks/useOutsideClick';
import { feedSortDirection, FeedSortBy, DASHBOARD_SORT_KEY, DEFAULT_FEED_SORT } from '../utils/feedSort';
import { Pagination } from '../components/Pagination';
import { deleteStopsProcessingMessage } from '../utils/feedTitle';
import { formatDateTime } from '../utils/format';
import { ConfirmModal } from '../components/Modal';
import QueryError from '../components/QueryError';
import { btnBase, btnLg, btnPrimary, btnSecondary } from '../components/buttonStyles';
import { focusRing, inputBase } from '../components/fieldStyles';
import { pageTitle } from '../components/pageStyles';

type DashboardView = 'podcasts' | 'episodes';
const DASHBOARD_VIEW_KEY = 'dashboardView';
const DASHBOARD_EPISODES_PER_PODCAST_KEY = 'dashboardEpisodesPerPodcast';

const DASHBOARD_VIEW_OPTIONS = [
  { value: 'podcasts' as const, label: 'Feeds', title: 'Group by feed' },
  { value: 'episodes' as const, label: 'Episodes', title: 'Show latest episodes per podcast' },
];

// Feeds per dashboard page. Divides evenly into the 1/2/3-column grid so a
// full page never leaves a ragged last row.
const FEEDS_PER_PAGE = 24;

// Labelled actions show from md; they sit beside the title only from lg.
const dashboardHeader = 'flex min-w-0 flex-col gap-4 mb-6 lg:flex-row lg:items-start lg:justify-between';
// Boxed keyboard-shortcut badge, matching the mockup's shortcut hints.
const kbdClass = 'rounded border border-border bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground';

function Dashboard() {
  const queryClient = useQueryClient();
  const [refreshingSlug, setRefreshingSlug] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [viewMode, setViewMode] = useLocalStorageState<'grid' | 'list'>('dashboardViewMode', 'grid');
  const [sortBy, setSortBy] = useLocalStorageState<FeedSortBy>(DASHBOARD_SORT_KEY, DEFAULT_FEED_SORT);
  const [dashboardView, setDashboardView] = useLocalStorageState<DashboardView>(DASHBOARD_VIEW_KEY, 'podcasts');
  const [episodesPerPodcastRaw, setEpisodesPerPodcast] = useLocalStorageState<number>(
    DASHBOARD_EPISODES_PER_PODCAST_KEY, DEFAULT_EPISODES_PER_PODCAST,
  );
  // Clamped defensively: a hand-edited or stale localStorage value could sit
  // outside the 1-10 range the select and the backend projection expect.
  const episodesPerPodcast = clampEpisodesPerPodcast(episodesPerPodcastRaw);
  const [actionError, setActionError] = useState<string | null>(null);
  // The modified OPML is what a podcast app imports, so it's offered here and
  // not only under Settings > Data Management.
  const [opmlExportOpen, setOpmlExportOpen] = useState(false);

  const [feedsPage, setFeedsPage] = useState(1);
  const isEpisodesView = dashboardView === 'episodes';
  // The server orders the whole list before slicing the page, so the browser
  // must not re-sort what it receives.
  const pageParams = {
    page: feedsPage, limit: FEEDS_PER_PAGE,
    sortBy: sortBy as FeedSortBy, sortDir: feedSortDirection(sortBy),
  };

  // Each view fetches only its own projection: the Podcasts grid never pays
  // for the episode projection, and Episodes never fetches a second bare list.
  const podcastsQuery = useQuery({
    ...feedsQueryOptionsFor(pageParams),
    enabled: !isEpisodesView,
    placeholderData: keepPreviousData,
  });
  const episodesQuery = useQuery({
    ...feedsQueryOptionsFor({
      ...pageParams, includeLatestEpisodes: true, episodesPerFeed: episodesPerPodcast,
    }),
    enabled: isEpisodesView,
    // Changing the per-podcast count keeps the current groups until the new
    // ones land, instead of blanking the list under an open menu.
    placeholderData: keepPreviousData,
  });

  const activeQuery = isEpisodesView ? episodesQuery : podcastsQuery;
  const { data, isLoading, error, isPlaceholderData } = activeQuery;
  const feeds = data?.feeds;
  const lastRefreshCompletedAt = data?.lastRefreshCompletedAt ?? null;
  const totalFeeds = data?.total ?? 0;
  const totalPages = data?.totalPages ?? 1;
  // A shrinking list strands the pager past the last page, and only the
  // response knows where the end now is. Adjusted during render, not in an
  // effect, so the next fetch already uses the corrected page.
  if (feedsPage > totalPages) setFeedsPage(totalPages);

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
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['feeds'] });
      setDeleteConfirm(null);
      if (result?.pending) setActionError(result.message);
    },
    onError: (err) => { setActionError((err as Error).message); },
  });

  const handleDelete = (slug: string) => {
    setActionError(null);
    setDeleteConfirm(slug);
  };

  const handleRefresh = (slug: string, options?: { force?: boolean }) => {
    refreshMutation.mutate({ slug, options });
  };

  const pageFeeds = feeds ?? [];

  const navigate = useNavigate();
  const searchRootRef = useRef<HTMLDivElement>(null);
  const { query, setQuery, debounced, ready, rows, current, setActive, onKeyDown, open, close, loading: searching, isError: searchFailed, refetch: retrySearch } = useUnifiedSearch('');

  const goToResult = (row: SearchResultRow) => { close(); navigate(row.to); };

  // Belt and suspenders: iOS Safari does not reliably blur a focused input
  // when the tap lands on a non-focusable element, so onBlur alone can miss
  // an outside tap; this document listener covers that case.
  useOutsideClick(searchRootRef, open, close, { touch: true });

  const showSearchPanel = open && query.length > 0;

  // grid-cols-2 starts at 600px: an unfolded foldable phone is 626px wide, 14px under sm.
  if (isLoading) {
    return (
      <div>
        <Skeleton className="h-[42px] w-full mb-6" />
        {viewMode === 'grid' ? (
          <SkeletonStatCards count={6} className="grid gap-4 grid-cols-1 min-[600px]:grid-cols-2 lg:grid-cols-3" />
        ) : (
          <SkeletonRows count={6} className="flex flex-col gap-2" />
        )}
      </div>
    );
  }

  if (error && (!data || isPlaceholderData)) {
    return (
      <div className="space-y-6">
        <h1 className={pageTitle}>Feeds</h1>
        <QueryError title="Could not load feeds." onRetry={() => void activeQuery.refetch()} retrying={activeQuery.isFetching} />
      </div>
    );
  }

  const dashboardViewLabel = DASHBOARD_VIEW_OPTIONS.find((option) => option.value === dashboardView)?.label ?? 'Feeds';

  return (
    <div>
      {error && <div className="mb-6"><QueryError title="Feeds could not be refreshed. Showing the last loaded list." onRetry={() => void activeQuery.refetch()} retrying={activeQuery.isFetching} /></div>}
      <div
        className="relative mb-6"
        ref={searchRootRef}
        onBlur={(e) => {
          // onBlur (desktop tab-away and most clicks) plus the document
          // listener above (iOS Safari does not blur on a non-focusable tap).
          if (!e.currentTarget.contains(e.relatedTarget as Node)) close();
        }}
      >
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            role="combobox"
            aria-expanded={showSearchPanel && rows.length > 0}
            aria-controls={showSearchPanel ? 'dashboard-search-results' : undefined}
            aria-activedescendant={showSearchPanel ? rows[current]?.key : undefined}
            aria-autocomplete="list"
            aria-label="Search shows, episodes and transcripts"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onFocus={() => setQuery(query)}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { close(); return; }
              onKeyDown(e, goToResult);
            }}
            placeholder="Search shows, episodes and transcripts"
            className={`${inputBase} w-full py-2.5 pl-10 pr-16`}
          />
          <span className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 gap-1 sm:flex">
            <kbd className={kbdClass}>Ctrl/Cmd+K</kbd>
          </span>
        </div>
        {showSearchPanel && (
          <div
            className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-border bg-card shadow-lg"
            // Rows are not focusable, so a plain mousedown would blur the input and
            // the onBlur above would unmount them before their click could fire.
            onMouseDown={(e) => e.preventDefault()}
          >
            <ul id="dashboard-search-results" role="listbox" aria-label="Results" className="max-h-[60vh] overflow-y-auto py-1">
              <SearchResults rows={rows} activeIndex={current} onHover={setActive} onSelect={goToResult} ready={ready} loading={searching} failed={searchFailed} />
            </ul>
            {searchFailed && <button type="button" className="px-4 py-3 text-primary" onClick={() => void retrySearch()}>Retry search</button>}
            <div className="flex items-center justify-between gap-2 border-t border-border bg-secondary/50 px-4 py-2 text-sm">
              <span className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
                <kbd className={kbdClass}>&#8593;</kbd><kbd className={kbdClass}>&#8595;</kbd> move
                <kbd className={kbdClass}>&#8629;</kbd> open
                <kbd className={kbdClass}>esc</kbd> close
              </span>
              <Link
                to={ready ? `/search?q=${encodeURIComponent(debounced)}` : '/search'}
                className={`text-primary hover:underline ${focusRing}`}
              >
                Advanced search
              </Link>
            </div>
          </div>
        )}
      </div>

      <div className={dashboardHeader}>
        <div className="w-full lg:w-auto flex min-w-0 flex-wrap items-baseline gap-x-3">
          <h1 className={pageTitle}>Feeds</h1>
          {lastRefreshCompletedAt && (
            <span
              className="text-sm text-muted-foreground"
              title="When the last check of all feeds finished"
            >
              Updated {formatDateTime(lastRefreshCompletedAt)}
            </span>
          )}
        </div>
        <div className="w-full md:w-auto flex gap-2 items-center justify-between md:justify-start overflow-x-auto no-scrollbar md:overflow-visible">
          <div className="hidden md:block">
            <SegmentedToggle
              options={DASHBOARD_VIEW_OPTIONS}
              value={dashboardView}
              onChange={setDashboardView}
              ariaLabel="Feeds view"
              variant="toolbar"
            />
          </div>
          <div className="md:hidden">
            <DropdownMenu
              triggerLabel={<PanelsTopLeft className="h-5 w-5" aria-hidden="true" />}
              triggerClassName={`${btnBase} ${btnSecondary}`}
              chevronClassName="hidden"
              ariaLabel={`View by ${dashboardViewLabel}`}
              title={`View by ${dashboardViewLabel}`}
              align="left"
              centerOnMobile={false}
              items={DASHBOARD_VIEW_OPTIONS.map((option) => ({
                title: option.label,
                checked: dashboardView === option.value,
                onClick: () => setDashboardView(option.value),
              }))}
            />
          </div>
          <div className="flex gap-2 items-center shrink-0">
          <DashboardControlsMenu
            dashboardView={dashboardView}
            viewMode={viewMode}
            onViewModeChange={setViewMode}
            sortBy={sortBy}
            onSortChange={(next) => { setSortBy(next); setFeedsPage(1); }}
            episodesPerPodcast={episodesPerPodcast}
            onEpisodesPerPodcastChange={(n) => setEpisodesPerPodcast(clampEpisodesPerPodcast(n))}
            perPodcastMin={MIN_EPISODES_PER_PODCAST}
            perPodcastMax={MAX_EPISODES_PER_PODCAST}
          />
          <DropdownMenu
            triggerLabel={
              <>
                <RefreshCw className="w-5 h-5 md:hidden" aria-hidden="true" />
                <span className="hidden md:inline">{refreshAllMutation.isPending ? 'Refreshing...' : 'Refresh all'}</span>
              </>
            }
            triggerClassName={`${btnBase} ${btnSecondary} shrink-0`}
            chevronClassName="w-4 h-4 hidden md:block"
            disabled={refreshAllMutation.isPending}
            title="Refresh all feeds"
            ariaLabel="Refresh all feeds"
            items={[
              {
                title: 'Refresh all',
                subtitle: 'Check every feed for new episodes',
                onClick: () => refreshAllMutation.mutate(undefined),
              },
              {
                title: 'Force refresh all',
                subtitle: 'Bypass cache on every feed',
                onClick: () => refreshAllMutation.mutate({ force: true }),
              },
            ]}
          />
          <button
            type="button"
            onClick={() => setOpmlExportOpen(true)}
            className={`${btnBase} ${btnSecondary} shrink-0`}
            title="Export feeds as OPML for a podcast app"
            aria-label="Export OPML"
          >
            <Download className="h-5 w-5 md:hidden" aria-hidden="true" />
            <span className="hidden md:inline">Export OPML</span>
          </button>
          <Link
            to="/add"
            className={`${btnBase} touch-target ${btnPrimary} shrink-0`}
            title="Add feed"
            aria-label="Add feed"
          >
            <Plus className="w-5 h-5 md:w-4 md:h-4" aria-hidden="true" />
            <span className="hidden md:inline">Add feed</span>
          </Link>
          </div>
        </div>
      </div>

      {totalFeeds === 0 ? (
        <div className="text-center py-12 bg-card rounded-lg border border-border">
          <p className="text-muted-foreground mb-4">No feeds yet.</p>
          <Link
            to="/add"
            className={`${btnLg} touch-target ${btnPrimary}`}
          >
            <Plus className="w-4 h-4" aria-hidden="true" />
            Add your first feed
          </Link>
          <p className="text-sm text-muted-foreground mt-4">
            Find podcast RSS feeds at{' '}
            <a
              href="https://podcastindex.org/"
              target="_blank"
              rel="noopener noreferrer"
              className={`text-primary hover:underline ${focusRing}`}
            >
              podcastindex.org
            </a>
          </p>
        </div>
      ) : dashboardView === 'episodes' ? (
        episodesQuery.isLoading ? (
          <SkeletonRows count={4} className="flex flex-col gap-4" />
        ) : episodesQuery.error ? (
          <div className="text-center py-12">
            <p className="text-destructive">Could not load episodes.</p>
            <p className="text-sm text-muted-foreground mt-2">{(episodesQuery.error as Error).message}</p>
          </div>
        ) : (
          <DashboardEpisodeGroups feeds={pageFeeds} episodesPerPodcast={episodesPerPodcast} />
        )
      ) : viewMode === 'grid' ? (
        <div className="grid gap-4 grid-cols-1 min-[600px]:grid-cols-2 lg:grid-cols-3">
          {pageFeeds.map((feed) => (
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
          {pageFeeds.map((feed) => (
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

      {totalFeeds > 0 && (
        <Pagination
          page={feedsPage}
          totalPages={totalPages}
          total={totalFeeds}
          onPage={setFeedsPage}
        />
      )}

      {(deleteConfirm || actionError) && (
        <div className="fixed bottom-4 right-[max(1rem,env(safe-area-inset-right))] flex flex-col items-end gap-2">
          {actionError && !deleteConfirm && (
            <div className="max-w-sm bg-destructive/10 border border-destructive text-destructive rounded-lg p-4 shadow-lg text-sm flex items-start gap-3">
              <span className="flex-1">{actionError}</span>
              <CloseButton onClick={() => setActionError(null)} label="Dismiss error" className="shrink-0" />
            </div>
          )}
          {deleteConfirm && (
            <ConfirmModal title={`Delete ${feeds?.find(f => f.slug === deleteConfirm)?.title || deleteConfirm}?`}
              confirmLabel="Delete feed" pending={deleteMutation.isPending}
              onCancel={() => { setDeleteConfirm(null); setActionError(null); }} onConfirm={() => deleteMutation.mutate(deleteConfirm)}>
              <p>This removes the feed and its downloaded files.</p>
              {feeds?.find(f => f.slug === deleteConfirm)?.feedType === 'local' && <p className="text-warning">Uploaded originals will also be deleted.</p>}
              {actionError && <p role="alert" className="text-destructive">{actionError}</p>}
              {(() => {
                const processingCount = feeds?.find((f) => f.slug === deleteConfirm)?.statusCounts?.processing ?? 0;
                const message = deleteStopsProcessingMessage(processingCount);
                return message && (
                  <p className="text-sm text-warning mt-1">{message}</p>
                );
              })()}
            </ConfirmModal>
          )}
        </div>
      )}

      <FeedOpmlExportDialog open={opmlExportOpen} onClose={() => setOpmlExportOpen(false)} />
    </div>
  );
}

export default Dashboard;
