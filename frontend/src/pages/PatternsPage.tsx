import { useState, useEffect, useMemo } from 'react';
import PageTabs from '../components/PageTabs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';
import { useUrlParams, useUrlSearch, useUrlSort } from '../hooks/useUrlParams';
import {
  getPatterns, getPatternStats, AdPattern, updatePattern,
  protectPattern, unprotectPattern, PATTERN_SOURCE_COMMUNITY,
} from '../api/patterns';
import {
  triggerCommunitySync, getCommunitySyncStatus,
} from '../api/community';
import PatternDetailModal from '../components/PatternDetailModal';
import PatternMergeSuggestions from '../components/PatternMergeSuggestions';
import { SkeletonRows } from '../components/Skeleton';
import { Pagination } from '../components/Pagination';
import { tableBody, tableCard, tableHead, tableRow, tableLink, tdEmpty, thBase, rowCard } from '../components/tableStyles';
import { SortHeader } from '../components/SortHeader';
import SortControls from '../components/SortControls';
import { ScopeBadge } from '../components/ScopeBadge';
import { CommunityBadge } from '../components/CommunityBadge';
import { PatternTrustBadge } from '../components/PatternTrustBadge';
import { Badge } from '../components/Badge';
import { SegmentCategoryBadge } from '../components/SegmentCategoryBadge';
import { PatternImportDialog } from '../components/PatternImportDialog';
import { PatternExportDialog } from '../components/PatternExportDialog';
import { formatDate } from '../utils/format';
import { audioSummaryLabel } from '../utils/patternAudio';
import {
  SEGMENT_CATEGORY_FILTER_OPTIONS, UNSET_CATEGORY,
} from '../utils/segmentCategory';
import AdReviewTab from './patterns/AdReviewTab';
import DetectedAdsTab from './patterns/DetectedAdsTab';
import CleanupTab from './patterns/CleanupTab';
import { SponsorsSection } from './SponsorsPage';
import { getPatternCleanupStatus, patternCleanupQueryKey } from '../api/patternCleanup';
import UnresolvedCorrectionsPanel from './patterns/UnresolvedCorrectionsPanel';
import { RefreshCw } from 'lucide-react';
import StatTile from '../components/StatTile';
import Checkbox from '../components/Checkbox';
import {
  SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, type SegmentCategory,
} from '../utils/segmentCategory';
import { ActiveBadge } from '../components/ActiveBadge';
import { btnBase, btnOutline } from '../components/buttonStyles';
import { focusRing, inputBase, selectBase, filterGrid, filterLabel, filterControl } from '../components/fieldStyles';
import { countPill, tint } from '../components/badgeStyles';
import { pageHeader, pageTitle } from '../components/pageStyles';
import { tableActionBtn } from '../components/rowActionStyles';
import { DemoDisabled } from '../components/DemoDisabled';

type ScopeFilter = 'all' | 'global' | 'network' | 'podcast';
type OriginFilter = 'all' | 'auto' | 'user';
type SourceFilter = 'all' | 'local' | 'community' | 'imported';
type PatternsTab = 'patterns' | 'ad-review' | 'detected-ads' | 'cleanup' | 'sponsors';
const OTHER_TABS: readonly string[] = ['ad-review', 'detected-ads', 'cleanup', 'sponsors'];
const SORT_FIELDS: readonly (keyof AdPattern)[] = [
  'id', 'scope', 'sponsor', 'confirmation_count', 'false_positive_count', 'created_at', 'last_matched_at',
];
const SCOPE_FILTERS: readonly ScopeFilter[] = ['all', 'global', 'network', 'podcast'];
const ORIGIN_FILTERS: readonly OriginFilter[] = ['all', 'auto', 'user'];
const SOURCE_FILTERS: readonly SourceFilter[] = ['all', 'local', 'community', 'imported'];

// Page, filters, search and sort live in the URL so Back and reload return to the same place.
const PATTERN_PARAM_DEFAULTS = {
  page: '1', scope: 'all', origin: 'all', source: 'all', category: '', inactive: '',
};

// Column widths in px, Sponsor (index 2) unsized so it takes the spare width.
const COLUMN_WIDTHS = ['w-[52px]', 'w-[148px]', '', 'w-[128px]', 'w-[124px]', 'w-[96px]', 'w-[84px]', 'w-[96px]', 'w-[96px]', 'w-[72px]', 'w-[84px]'];

function PatternsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const activeTab: PatternsTab =
    tabParam && OTHER_TABS.includes(tabParam) ? (tabParam as PatternsTab) : 'patterns';
  const { read, readOneOf, readPage, write } = useUrlParams(PATTERN_PARAM_DEFAULTS, { resetKey: 'page' });
  const scopeFilter = readOneOf('scope', SCOPE_FILTERS, 'all');
  const originFilter = readOneOf('origin', ORIGIN_FILTERS, 'all');
  const sourceFilter = readOneOf('source', SOURCE_FILTERS, 'all');
  const categoryFilter = read('category');
  const showInactive = read('inactive') === '1';
  // ?q= belongs to the visible tab; the sponsors tab reads it itself.
  const [searchInput, setSearchInput, searchQuery] = useUrlSearch('q', { enabled: activeTab === 'patterns' });
  const [selectedPattern, setSelectedPattern] = useState<AdPattern | null>(null);
  const { sortBy: sortField, sortDirection, onSort: handleSort } = useUrlSort(SORT_FIELDS, 'created_at', 'desc');
  const [importOpen, setImportOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const limit = 20;


  const switchTab = (tab: PatternsTab) => {
    setSearchParams(tab === 'patterns' ? {} : { tab });
  };

  const queryClient = useQueryClient();
  // A pattern's category decides the segment action for every future match,
  // so it is editable from the list rather than only inside the detail modal.
  const categoryMutation = useMutation({
    mutationFn: ({ id, category }: { id: number; category: SegmentCategory | null }) =>
      updatePattern(id, { category }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['patterns'] }),
  });

  const { data: patterns, isLoading, error, refetch } = useQuery({
    queryKey: ['patterns', scopeFilter, showInactive, sourceFilter],
    queryFn: () => getPatterns({
      scope: scopeFilter === 'all' ? undefined : scopeFilter,
      active: showInactive ? undefined : true,
      source: sourceFilter === 'all' ? undefined : sourceFilter,
    }),
  });

  const { data: syncStatus, refetch: refetchSyncStatus } = useQuery({
    queryKey: ['communitySyncStatus'],
    queryFn: getCommunitySyncStatus,
    refetchInterval: 60_000,
  });

  async function handleSyncNow() {
    try {
      await triggerCommunitySync();
      refetchSyncStatus();
      refetch();
    } catch (e) {
      // Errors surface via /community-patterns/sync-status lastError.
      console.error('Sync failed', e);
    }
  }

  async function handleToggleProtect(pattern: AdPattern) {
    try {
      if (pattern.protected_from_sync) {
        await unprotectPattern(pattern.id);
      } else {
        await protectPattern(pattern.id);
      }
      refetch();
    } catch (e) {
      console.error('Protect toggle failed', e);
    }
  }

  // Only the pending count feeds the tab badge here; a minute-old count is fine.
  const { data: cleanupStatus } = useQuery({
    queryKey: patternCleanupQueryKey,
    queryFn: getPatternCleanupStatus,
    staleTime: 60_000,
  });
  const cleanupPending = cleanupStatus?.pending.total ?? 0;

  const { data: stats } = useQuery({
    queryKey: ['patternStats'],
    queryFn: getPatternStats,
  });

  // Handle ?id= query param to open pattern detail. setSearchParams writes
  // router state, so this lives in an effect rather than running during render.
  useEffect(() => {
    // Consume ?id= only while the Patterns tab is visible: the detail modal renders inside the patterns
    // conditional, so consuming it on the ad-review tab would eat the deep link. Switching tabs re-runs this
    // effect and opens the modal then.
    if (activeTab !== 'patterns') return;
    const idParam = searchParams.get('id');
    if (idParam && patterns) {
      const pattern = patterns.find(p => p.id === parseInt(idParam));
      if (pattern) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setSelectedPattern(pattern);
        // Clear only the id param, preserve other params
        setSearchParams((prev) => {
          const next = new URLSearchParams(prev);
          next.delete('id');
          return next;
        });
      }
    }
  }, [activeTab, patterns, searchParams, setSearchParams]);

  // Memoized so unrelated state changes (modal open, page flips) don't
  // re-filter and re-sort; the copy keeps the sort off any shared array.
  const sortedPatterns = useMemo(() => {
    const filtered = patterns?.filter(pattern => {
      if (originFilter === 'user' && pattern.created_by !== 'user') return false;
      if (originFilter === 'auto' && pattern.created_by === 'user') return false;
      if (categoryFilter === UNSET_CATEGORY && pattern.category) return false;
      if (categoryFilter && categoryFilter !== UNSET_CATEGORY
          && pattern.category !== categoryFilter) return false;
      if (searchQuery) {
        const query = searchQuery.toLowerCase();
        return (
          pattern.id.toString().includes(query) ||
          pattern.sponsor?.toLowerCase().includes(query) ||
          pattern.text_template?.toLowerCase().includes(query) ||
          pattern.network_id?.toLowerCase().includes(query) ||
          pattern.podcast_id?.toLowerCase().includes(query)
        );
      }
      return true;
    });
    return filtered && [...filtered].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];

      if (aVal === null || aVal === undefined) return 1;
      if (bVal === null || bVal === undefined) return -1;

      let comparison: number;
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        comparison = aVal.localeCompare(bVal);
      } else if (typeof aVal === 'number' && typeof bVal === 'number') {
        comparison = aVal - bVal;
      } else {
        comparison = String(aVal).localeCompare(String(bVal));
      }

      return sortDirection === 'asc' ? comparison : -comparison;
    });
  }, [patterns, originFilter, categoryFilter, searchQuery, sortField, sortDirection]);

  // Pagination
  const totalPages = Math.ceil((sortedPatterns?.length || 0) / limit);
  const page = readPage('page', totalPages);
  const setPage = (next: number) => write({ page: String(next) });
  const paginatedPatterns = sortedPatterns?.slice((page - 1) * limit, page * limit);

  const categorySelect = (pattern: AdPattern, className: string) => (
    <select
      aria-label={`Category for pattern ${pattern.id}`}
      value={pattern.category ?? ''}
      disabled={categoryMutation.isPending}
      onChange={(e) => categoryMutation.mutate({
        id: pattern.id,
        category: e.target.value === '' ? null : (e.target.value as SegmentCategory),
      })}
      className={`${selectBase} ${className} disabled:opacity-50`}
    >
      <option value="">Uncategorized</option>
      {SEGMENT_CATEGORIES.map((c) => (
        <option key={c} value={c}>{SEGMENT_CATEGORY_LABELS[c]}</option>
      ))}
    </select>
  );

  return (
    <div>
      <div className={pageHeader}>
        <h1 className={pageTitle}>Patterns</h1>
        {activeTab === 'patterns' && (
          <div className="flex flex-wrap items-center gap-2 sm:gap-3 text-sm sm:justify-end">
            <span className="text-muted-foreground">
              {sortedPatterns?.length || 0} patterns
            </span>
            {syncStatus?.lastRun && (
              <DemoDisabled>
                <button
                  type="button"
                  onClick={handleSyncNow}
                  className={`${btnBase} ${btnOutline}`}
                  title={syncStatus.lastError
                    ? `Last error: ${syncStatus.lastError}`
                    : `Last synced ${new Date(syncStatus.lastRun).toLocaleString()}. Sync now.`}
                >
                  <RefreshCw className="w-4 h-4" aria-hidden="true" /> Synced
                  <span className="hidden sm:inline">
                    {' '}{new Date(syncStatus.lastRun).toLocaleDateString()}
                  </span>
                </button>
              </DemoDisabled>
            )}
            <button
              type="button"
              onClick={() => setImportOpen(true)}
              className={`${btnBase} ${btnOutline}`}
            >
              Import
            </button>
            <button
              type="button"
              onClick={() => setExportOpen(true)}
              className={`${btnBase} ${btnOutline}`}
            >
              Export
            </button>
          </div>
        )}
      </div>
      <PatternImportDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onComplete={() => refetch()}
      />
      <PatternExportDialog
        open={exportOpen}
        patterns={sortedPatterns || []}
        onClose={() => setExportOpen(false)}
      />

      <PageTabs id="patterns" label="Pattern sections" active={activeTab} onChange={switchTab} tabs={[
        { value: 'patterns', label: 'Patterns' },
        { value: 'detected-ads', label: 'Detected ads' },
        { value: 'ad-review', label: 'Ad review' },
        { value: 'cleanup', label: 'Cleanup', accessibleLabel: cleanupPending > 0 ? `Cleanup, ${cleanupPending} pending` : undefined,
          badge: cleanupPending > 0 ? <span className={`ml-2 ${countPill} ${tint.warning}`}>{cleanupPending}</span> : undefined },
        { value: 'sponsors', label: 'Sponsors' },
      ]} />
      <div role="tabpanel" id={`patterns-panel-${activeTab}`} aria-labelledby={`patterns-tab-${activeTab}`} tabIndex={0}>
      {activeTab === 'detected-ads' && <DetectedAdsTab />}
      {activeTab === 'ad-review' && <AdReviewTab />}
      {activeTab === 'cleanup' && <CleanupTab />}
      {activeTab === 'sponsors' && <SponsorsSection />}

      {activeTab === 'patterns' && (<>

      <UnresolvedCorrectionsPanel />

      {isLoading && <SkeletonRows count={6} />}
      {error && (
        <div className="text-center py-12">
          <p className="text-destructive">{patterns ? 'Patterns could not be refreshed. Showing the last loaded list.' : 'Could not load patterns.'}</p>
        </div>
      )}
      {!isLoading && patterns && (<>

      {/* Stats Summary */}
      {stats && (
        <section aria-label="Pattern statistics" className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4 mb-6">
          <StatTile label="Total" value={stats.total} />
          <StatTile label="Active" value={stats.active} tone="success" />
          <StatTile label="Global" value={stats.by_scope.global} />
          <StatTile label="Network" value={stats.by_scope.network} />
          <StatTile label="Podcast" value={stats.by_scope.podcast} />
          <StatTile label="Unknown sponsor" value={stats.no_sponsor} tone={stats.no_sponsor > 0 ? 'warning' : 'default'} />
          <StatTile label="High false positives" value={stats.high_false_positive_count}
            tone={stats.high_false_positive_count > 0 ? 'destructive' : 'default'} />
        </section>
      )}

      {/* Merge suggestions (#399): same-sponsor near-duplicate clusters */}
      <PatternMergeSuggestions onMerged={() => refetch()} />

      {/* Filters */}
      <div className="bg-card rounded-lg border border-border p-4 mb-6">
        <div className={`${filterGrid} lg:grid-cols-4`}>
          {/* Scope filter */}
          <div className="min-w-0">
            <label htmlFor="patterns-scope" className={filterLabel}>Scope</label>
            <select
              id="patterns-scope"
              value={scopeFilter}
              onChange={(e) => write({ scope: e.target.value })}
              className={`${filterControl} ${selectBase}`}
            >
              <option value="all">All</option>
              <option value="global">Global</option>
              <option value="network">Network</option>
              <option value="podcast">Podcast</option>
            </select>
          </div>

          {/* Origin filter */}
          <div className="min-w-0">
            <label htmlFor="patterns-origin" className={filterLabel}>Origin</label>
            <select
              id="patterns-origin"
              value={originFilter}
              onChange={(e) => write({ origin: e.target.value })}
              className={`${filterControl} ${selectBase}`}
            >
              <option value="all">All</option>
              <option value="auto">Auto</option>
              <option value="user">Manual</option>
            </select>
          </div>

          {/* Source filter */}
          <div className="min-w-0">
            <label htmlFor="patterns-source" className={filterLabel}>Source</label>
            <select
              id="patterns-source"
              value={sourceFilter}
              onChange={(e) => write({ source: e.target.value })}
              className={`${filterControl} ${selectBase}`}
            >
              <option value="all">All</option>
              <option value="local">Local</option>
              <option value="community">Community</option>
              <option value="imported">Imported</option>
            </select>
          </div>

          {/* Category filter */}
          <div className="min-w-0">
            <label htmlFor="patterns-category" className={filterLabel}>Category</label>
            <select
              id="patterns-category"
              value={categoryFilter}
              onChange={(e) => write({ category: e.target.value })}
              className={`${filterControl} ${selectBase}`}
            >
              {SEGMENT_CATEGORY_FILTER_OPTIONS.map(([value, label]) => (
                <option key={value || 'all'} value={value}>{label}</option>
              ))}
            </select>
          </div>

          {/* Search */}
          <div className="min-w-0 min-[375px]:col-span-2">
            <label htmlFor="patterns-search" className={filterLabel}>Search</label>
            <input
              id="patterns-search"
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by sponsor, text, network..."
              aria-label="Search patterns"
              className={`${filterControl} ${inputBase}`}
            />
          </div>

          {/* Show inactive toggle */}
          <Checkbox
            className="min-h-11"
            checked={showInactive}
            onChange={(v) => write({ inactive: v ? '1' : '' })}
            label="Show inactive"
            labelClassName="text-sm text-muted-foreground"
          />
        </div>
      </div>

      <SortControls className="xl:hidden" field={sortField} direction={sortDirection} onSort={handleSort} options={[
        { value: 'created_at', label: 'Created' }, { value: 'last_matched_at', label: 'Last matched' },
        { value: 'id', label: 'ID' }, { value: 'scope', label: 'Scope' }, { value: 'sponsor', label: 'Sponsor' },
        { value: 'confirmation_count', label: 'Confirmed' }, { value: 'false_positive_count', label: 'False positives' },
      ]} />
      {/* Card layout: the 11-column table needs about 1140 CSS px with touch-sized controls */}
      <div className="xl:hidden space-y-3 mb-4">
        {paginatedPatterns?.map((pattern) => (
          <div
            key={pattern.id}
            className={`${rowCard} cursor-pointer hover:bg-accent/50 transition-colors`}
            onClick={() => setSelectedPattern(pattern)}
          >
            <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
              <span className="text-xs font-mono text-muted-foreground">#{pattern.id}</span>
              <div className="flex items-center gap-2 flex-wrap">
                <ScopeBadge pattern={pattern} truncate />
                <SegmentCategoryBadge category={pattern.category} />
                {pattern.created_by === 'user' && (
                  <Badge tone="warning" title="Manual">Manual</Badge>
                )}
                {pattern.source === PATTERN_SOURCE_COMMUNITY && pattern.community_id && (
                  <CommunityBadge
                    communityId={pattern.community_id}
                    version={pattern.version}
                    protected={!!pattern.protected_from_sync}
                  />
                )}
                {pattern.source === PATTERN_SOURCE_COMMUNITY && (
                  <PatternTrustBadge trust={pattern.trust} />
                )}
                <ActiveBadge active={pattern.is_active} />
              </div>
            </div>
            {pattern.source === PATTERN_SOURCE_COMMUNITY && (
              <div className="flex items-center gap-2 mb-2">
                <DemoDisabled>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); handleToggleProtect(pattern); }}
                    className={`${btnBase} ${btnOutline}`}
                  >
                    {pattern.protected_from_sync ? 'Unprotect' : 'Protect from sync'}
                  </button>
                </DemoDisabled>
              </div>
            )}
            <button
              type="button"
              aria-label={`View pattern ${pattern.id}: ${pattern.sponsor || 'Unknown sponsor'}`}
              className={`${tableLink} text-left text-sm font-medium mb-1`}
              onClick={(e) => { e.stopPropagation(); setSelectedPattern(pattern); }}
            >
              {pattern.sponsor || '(Unknown)'}
            </button>
            {pattern.text_template && (
              <div className="text-xs text-muted-foreground truncate mb-3">
                {pattern.text_template.substring(0, 80)}...
              </div>
            )}
            {/* Stops the card click: the select is its own control */}
            <div className="mb-3" onClick={(e) => e.stopPropagation()}>
              {categorySelect(pattern, 'w-full')}
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span className="text-success">
                Confirmed: {pattern.confirmation_count}
              </span>
              <span className={pattern.false_positive_count > 0 ? 'text-destructive' : ''}>
                False positives: {pattern.false_positive_count}
              </span>
              <span>Created: {formatDate(pattern.created_at)}</span>
              <span>Last matched: {formatDate(pattern.last_matched_at)}</span>
            </div>
            <div data-testid={`pattern-card-audio-${pattern.id}`} className="mt-2 text-xs text-muted-foreground">
              Audio: {audioSummaryLabel(pattern.audio)}
            </div>
          </div>
        ))}
        {paginatedPatterns?.length === 0 && (
          <div className={`${rowCard} p-8 text-center text-muted-foreground`}>
            {patterns.length === 0 ? 'No patterns yet.' : 'No patterns match these filters.'}
          </div>
        )}
      </div>

      {/* Desktop Table Layout */}
      <div className={`hidden xl:block ${tableCard}`}>
        <div className="overflow-x-auto" role="region" aria-label="Patterns table" tabIndex={0}>
          <table className="w-full table-fixed min-w-[1140px]">
            <colgroup>
              {COLUMN_WIDTHS.map((w, i) => (
                <col key={i} className={w} />
              ))}
            </colgroup>
            <thead className={tableHead}>
              <tr>
                <SortHeader field="id" label="ID" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="scope" label="Scope" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="sponsor" label="Sponsor" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className={`${thBase} px-2 text-left`}>
                  Category
                </th>
                <th className={`${thBase} px-2 text-left`}>
                  Audio
                </th>
                <SortHeader field="confirmation_count" label="Confirmed" className="px-2 whitespace-nowrap" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="false_positive_count" label="False pos." className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="created_at" label="Created" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="last_matched_at" label="Last matched" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className={`${thBase} px-2 text-left`}>
                  Status
                </th>
                <th className={`${thBase} px-2 text-left`}>
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className={tableBody}>
              {paginatedPatterns?.map((pattern) => (
                <tr
                  key={pattern.id}
                  className={`${tableRow} cursor-pointer`}
                  onClick={() => setSelectedPattern(pattern)}
                >
                  <td className="px-2 py-3 whitespace-nowrap text-sm font-mono text-muted-foreground">
                    #{pattern.id}
                  </td>
                  <td className="px-2 py-3 text-sm overflow-hidden">
                    <div className="flex items-center gap-1 flex-wrap">
                      <ScopeBadge pattern={pattern} truncate />
                      <SegmentCategoryBadge category={pattern.category} />
                      {pattern.created_by === 'user' && (
                        <Badge tone="warning" title="Manual">Manual</Badge>
                      )}
                      {pattern.source === PATTERN_SOURCE_COMMUNITY && pattern.community_id && (
                        <CommunityBadge
                          communityId={pattern.community_id}
                          version={pattern.version}
                          protected={!!pattern.protected_from_sync}
                        />
                      )}
                      {pattern.source === PATTERN_SOURCE_COMMUNITY && (
                        <PatternTrustBadge trust={pattern.trust} />
                      )}
                    </div>
                  </td>
                  <td className="px-2 py-3 text-sm overflow-hidden">
                    <button
                      type="button"
                      aria-label={`View pattern ${pattern.id}: ${pattern.sponsor || 'Unknown sponsor'}`}
                      className={`${tableLink} max-w-full text-left text-sm font-medium truncate`}
                      onClick={(e) => { e.stopPropagation(); setSelectedPattern(pattern); }}
                    >
                      {pattern.sponsor || '(Unknown)'}
                    </button>
                    {pattern.text_template && (
                      <div className="text-xs text-muted-foreground truncate">
                        {pattern.text_template.substring(0, 60)}...
                      </div>
                    )}
                  </td>
                  {/* Stops the row click: the select is its own control, not
                      a way into the detail modal. */}
                  <td className="px-2 py-3 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                    {categorySelect(pattern, 'w-full min-w-0')}
                  </td>
                  <td className="px-2 py-3 text-sm text-muted-foreground whitespace-nowrap">
                    {audioSummaryLabel(pattern.audio)}
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap">
                    <span className="text-sm text-success font-medium">
                      {pattern.confirmation_count}
                    </span>
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap">
                    <span className={`text-sm font-medium ${
                      pattern.false_positive_count > 0
                        ? 'text-destructive'
                        : 'text-muted-foreground'
                    }`}>
                      {pattern.false_positive_count}
                    </span>
                  </td>
                  <td className="px-2 py-3 text-sm text-muted-foreground whitespace-nowrap">
                    {formatDate(pattern.created_at)}
                  </td>
                  <td className="px-2 py-3 text-sm text-muted-foreground whitespace-nowrap">
                    {formatDate(pattern.last_matched_at)}
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap">
                    <ActiveBadge active={pattern.is_active} />
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap text-xs">
                    {pattern.source === PATTERN_SOURCE_COMMUNITY && (
                      <DemoDisabled>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); handleToggleProtect(pattern); }}
                          className={`${tableActionBtn} ${btnOutline} ${focusRing}`}
                        >
                          {pattern.protected_from_sync ? 'Unprotect' : 'Protect'}
                        </button>
                      </DemoDisabled>
                    )}
                  </td>
                </tr>
              ))}
              {paginatedPatterns?.length === 0 && (
                <tr>
                  <td colSpan={11} className={tdEmpty}>
                    {patterns.length === 0 ? 'No patterns yet.' : 'No patterns match these filters.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      <Pagination page={page} totalPages={totalPages} total={sortedPatterns?.length || 0} onPage={setPage} />

      {/* Detail Modal */}
      {selectedPattern && (
        <PatternDetailModal
          pattern={selectedPattern}
          onClose={() => setSelectedPattern(null)}
          onSave={() => {
            refetch();
            setSelectedPattern(null);
          }}
        />
      )}

      </>)}
      </>)}
      </div>
    </div>
  );
}

export default PatternsPage;
