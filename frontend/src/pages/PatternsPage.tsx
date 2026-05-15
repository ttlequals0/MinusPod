import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  getPatterns, getPatternStats, AdPattern,
  submitPatternToCommunity, protectPattern, unprotectPattern,
} from '../api/patterns';
import {
  triggerCommunitySync, getCommunitySyncStatus,
} from '../api/community';
import PatternDetailModal from '../components/PatternDetailModal';
import LoadingSpinner from '../components/LoadingSpinner';
import { CommunityBadge } from '../components/CommunityBadge';
import { PatternImportDialog } from '../components/PatternImportDialog';
import { PatternExportDialog } from '../components/PatternExportDialog';

type ScopeFilter = 'all' | 'global' | 'network' | 'podcast';
type OriginFilter = 'all' | 'auto' | 'user';
type SourceFilter = 'all' | 'local' | 'community' | 'imported';
type SortDirection = 'asc' | 'desc';

function SortHeader({
  field,
  label,
  className,
  sortField,
  sortDirection,
  onSort,
}: {
  field: keyof AdPattern;
  label: string;
  className?: string;
  sortField: keyof AdPattern;
  sortDirection: SortDirection;
  onSort: (field: keyof AdPattern) => void;
}) {
  return (
    <th
      className={`py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider cursor-pointer hover:bg-accent/50 ${className || 'px-4'}`}
      onClick={() => onSort(field)}
    >
      <div className="flex items-center gap-1">
        {label}
        {sortField === field && (
          <span>{sortDirection === 'asc' ? '↑' : '↓'}</span>
        )}
      </div>
    </th>
  );
}

function PatternsPage() {
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>('all');
  const [originFilter, setOriginFilter] = useState<OriginFilter>('all');
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [showInactive, setShowInactive] = useState(false);
  const [selectedPattern, setSelectedPattern] = useState<AdPattern | null>(null);
  const [sortField, setSortField] = useState<keyof AdPattern>('created_at');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
  const [page, setPage] = useState(1);
  const [importOpen, setImportOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const limit = 20;
  const [searchParams, setSearchParams] = useSearchParams();

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

  async function handleSubmitToCommunity(pattern: AdPattern) {
    try {
      const result = await submitPatternToCommunity(pattern.id);
      if (result.too_large) {
        const blob = new Blob([JSON.stringify(result.payload, null, 2)], {
          type: 'application/json',
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = result.filename;
        a.click();
        URL.revokeObjectURL(url);
        alert('Pattern too large for prefilled PR URL. Downloaded as a file instead — open patterns/community/ and start a PR with this file.');
      } else {
        window.open(result.pr_url, '_blank', 'noopener');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'submit failed';
      alert(`Could not submit to community: ${msg}`);
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

  const { data: stats } = useQuery({
    queryKey: ['patternStats'],
    queryFn: getPatternStats,
  });

  const handleSort = (field: keyof AdPattern) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
    setPage(1); // Reset to first page on sort change
  };

  // Handle ?id= query param to open pattern detail. setSearchParams writes
  // router state, so this lives in an effect rather than running during render.
  useEffect(() => {
    const idParam = searchParams.get('id');
    if (idParam && patterns) {
      const pattern = patterns.find(p => p.id === parseInt(idParam));
      if (pattern) {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setSelectedPattern(pattern);
        // Clear the param after opening
        setSearchParams({});
      }
    }
  }, [patterns, searchParams, setSearchParams]);

  const filteredPatterns = patterns?.filter(pattern => {
    if (originFilter === 'user' && pattern.created_by !== 'user') return false;
    if (originFilter === 'auto' && pattern.created_by === 'user') return false;
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

  const sortedPatterns = filteredPatterns?.sort((a, b) => {
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

  // Pagination
  const totalPages = Math.ceil((sortedPatterns?.length || 0) / limit);
  const paginatedPatterns = sortedPatterns?.slice((page - 1) * limit, page * limit);

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString();
  };

  const getScopeBadge = (pattern: AdPattern) => {
    if (pattern.scope === 'global') {
      return <span className="px-2 py-0.5 text-xs rounded bg-blue-500/20 text-blue-600 dark:text-blue-400">Global</span>;
    } else if (pattern.scope === 'network') {
      return <span className="px-2 py-0.5 text-xs rounded bg-purple-500/20 text-purple-600 dark:text-purple-400">Network: {pattern.network_id}</span>;
    } else if (pattern.scope === 'podcast') {
      return (
        <span className="px-2 py-0.5 text-xs rounded bg-green-500/20 text-green-600 dark:text-green-400 truncate block">
          {pattern.podcast_name || 'Podcast'}
        </span>
      );
    }
    return null;
  };

  const getStatusBadge = (isActive: boolean) => {
    if (isActive) {
      return (
        <span className="px-2 py-0.5 text-xs rounded bg-green-500/20 text-green-600 dark:text-green-400">
          Active
        </span>
      );
    }
    return (
      <span className="px-2 py-0.5 text-xs rounded bg-red-500/20 text-red-600 dark:text-red-400">
        Inactive
      </span>
    );
  };

  // Generate page numbers with ellipsis for large page counts
  const getPageNumbers = (current: number, total: number): (number | 'ellipsis')[] => {
    const pages: (number | 'ellipsis')[] = [];
    if (total <= 7) {
      for (let i = 1; i <= total; i++) pages.push(i);
    } else {
      pages.push(1);
      if (current > 3) pages.push('ellipsis');
      for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
        pages.push(i);
      }
      if (current < total - 2) pages.push('ellipsis');
      pages.push(total);
    }
    return pages;
  };

  if (isLoading) {
    return <LoadingSpinner className="py-12" />;
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <p className="text-destructive">Failed to load patterns</p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <h1 className="text-2xl font-bold text-foreground">Ad Patterns</h1>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-muted-foreground">
            {sortedPatterns?.length || 0} patterns
          </span>
          {syncStatus?.lastRun && (
            <button
              type="button"
              onClick={handleSyncNow}
              className="px-2 py-1 rounded text-xs border border-border hover:bg-accent transition-colors"
              title={syncStatus.lastError ? `Last error: ${syncStatus.lastError}` : 'Sync now'}
            >
              ↻ synced {new Date(syncStatus.lastRun).toLocaleString()}
            </button>
          )}
          <button
            type="button"
            onClick={() => setImportOpen(true)}
            className="px-3 py-1.5 rounded border border-border hover:bg-accent transition-colors"
          >
            Import
          </button>
          <button
            type="button"
            onClick={() => setExportOpen(true)}
            className="px-3 py-1.5 rounded border border-border hover:bg-accent transition-colors"
          >
            Export
          </button>
        </div>
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

      {/* Stats Summary */}
      {stats && (
        <div className="bg-card rounded-lg border border-border p-4 mb-6">
          <h2 className="text-sm font-medium text-foreground mb-3">Pattern Statistics</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4 text-sm">
            <div>
              <p className="text-muted-foreground">Total</p>
              <p className="font-medium text-foreground">{stats.total}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Active</p>
              <p className="font-medium text-green-600 dark:text-green-400">{stats.active}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Global</p>
              <p className="font-medium text-foreground">{stats.by_scope.global}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Network</p>
              <p className="font-medium text-foreground">{stats.by_scope.network}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Podcast</p>
              <p className="font-medium text-foreground">{stats.by_scope.podcast}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Unknown Sponsor</p>
              <p className={`font-medium ${stats.no_sponsor > 0 ? 'text-yellow-600 dark:text-yellow-400' : 'text-foreground'}`}>
                {stats.no_sponsor}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">High False Pos.</p>
              <p className={`font-medium ${stats.high_false_positive_count > 0 ? 'text-red-600 dark:text-red-400' : 'text-foreground'}`}>
                {stats.high_false_positive_count}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="bg-card rounded-lg border border-border p-4 mb-6">
        <div className="flex flex-wrap gap-4 items-center">
          {/* Scope filter */}
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground">Scope:</label>
            <select
              value={scopeFilter}
              onChange={(e) => {
                setScopeFilter(e.target.value as ScopeFilter);
                setPage(1);
              }}
              className="px-3 py-1.5 text-sm bg-secondary border border-border rounded"
            >
              <option value="all">All</option>
              <option value="global">Global</option>
              <option value="network">Network</option>
              <option value="podcast">Podcast</option>
            </select>
          </div>

          {/* Origin filter */}
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground">Origin:</label>
            <select
              value={originFilter}
              onChange={(e) => {
                setOriginFilter(e.target.value as OriginFilter);
                setPage(1);
              }}
              className="px-3 py-1.5 text-sm bg-secondary border border-border rounded"
            >
              <option value="all">All</option>
              <option value="auto">Auto</option>
              <option value="user">Manual</option>
            </select>
          </div>

          {/* Source filter */}
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground">Source:</label>
            <select
              value={sourceFilter}
              onChange={(e) => {
                setSourceFilter(e.target.value as SourceFilter);
                setPage(1);
              }}
              className="px-3 py-1.5 text-sm bg-secondary border border-border rounded"
            >
              <option value="all">All</option>
              <option value="local">Local</option>
              <option value="community">Community</option>
              <option value="imported">Imported</option>
            </select>
          </div>

          {/* Search */}
          <div className="flex-1 min-w-[200px]">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setPage(1);
              }}
              placeholder="Search by sponsor, text, network..."
              className="w-full px-3 py-1.5 text-sm bg-secondary border border-border rounded"
            />
          </div>

          {/* Show inactive toggle */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => {
                setShowInactive(e.target.checked);
                setPage(1);
              }}
              className="rounded"
            />
            <span className="text-sm text-muted-foreground">Show inactive</span>
          </label>
        </div>
      </div>

      {/* Mobile Card Layout */}
      <div className="sm:hidden space-y-3 mb-4">
        {paginatedPatterns?.map((pattern) => (
          <div
            key={pattern.id}
            className="bg-card rounded-lg border border-border p-4 cursor-pointer hover:bg-accent/50 transition-colors"
            onClick={() => setSelectedPattern(pattern)}
          >
            <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
              <span className="text-xs font-mono text-muted-foreground">#{pattern.id}</span>
              <div className="flex items-center gap-2 flex-wrap">
                {getScopeBadge(pattern)}
                {pattern.created_by === 'user' && (
                  <span className="px-2 py-0.5 text-xs rounded bg-amber-500/20 text-amber-600 dark:text-amber-400">
                    Manual
                  </span>
                )}
                {pattern.source === 'community' && pattern.community_id && (
                  <CommunityBadge
                    communityId={pattern.community_id}
                    version={pattern.version}
                    protected={!!pattern.protected_from_sync}
                  />
                )}
                {getStatusBadge(pattern.is_active)}
              </div>
            </div>
            {(pattern.source === 'local' || pattern.source === 'community') && (
              <div className="flex items-center gap-2 mb-2">
                {pattern.source === 'local' && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); handleSubmitToCommunity(pattern); }}
                    className="px-2 py-1 text-xs rounded border border-border hover:bg-accent"
                  >
                    Submit to community
                  </button>
                )}
                {pattern.source === 'community' && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); handleToggleProtect(pattern); }}
                    className="px-2 py-1 text-xs rounded border border-border hover:bg-accent"
                  >
                    {pattern.protected_from_sync ? 'Unprotect' : 'Protect from sync'}
                  </button>
                )}
              </div>
            )}
            <div className="text-sm font-medium text-foreground mb-1">
              {pattern.sponsor || '(Unknown)'}
            </div>
            {pattern.text_template && (
              <div className="text-xs text-muted-foreground truncate mb-3">
                {pattern.text_template.substring(0, 80)}...
              </div>
            )}
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span className="text-green-600 dark:text-green-400">
                Confirmed: {pattern.confirmation_count}
              </span>
              <span className={pattern.false_positive_count > 0 ? 'text-red-600 dark:text-red-400' : ''}>
                False Pos: {pattern.false_positive_count}
              </span>
              <span className="ml-auto">
                {formatDate(pattern.last_matched_at)}
              </span>
            </div>
          </div>
        ))}
        {paginatedPatterns?.length === 0 && (
          <div className="bg-card rounded-lg border border-border p-8 text-center text-muted-foreground">
            No patterns found
          </div>
        )}
      </div>

      {/* Desktop Table Layout */}
      <div className="hidden sm:block bg-card rounded-lg border border-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed divide-y divide-border">
            <colgroup>
              <col className="w-[4%]" />
              <col className="w-[16%]" />
              <col className="w-[26%]" />
              <col className="w-[7%]" />
              <col className="w-[7%]" />
              <col className="w-[11%]" />
              <col className="w-[11%]" />
              <col className="w-[7%]" />
              <col className="w-[11%]" />
            </colgroup>
            <thead className="bg-muted/50">
              <tr>
                <SortHeader field="id" label="ID" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="scope" label="Scope" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="sponsor" label="Sponsor" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="confirmation_count" label="Confirmed" className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="false_positive_count" label="False Pos." className="px-2" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="created_at" label="Created" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="last_matched_at" label="Last Matched" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className="px-2 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Status
                </th>
                <th className="px-2 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {paginatedPatterns?.map((pattern) => (
                <tr
                  key={pattern.id}
                  className="hover:bg-accent/50 cursor-pointer transition-colors"
                  onClick={() => setSelectedPattern(pattern)}
                >
                  <td className="px-2 py-3 whitespace-nowrap text-sm font-mono text-muted-foreground">
                    #{pattern.id}
                  </td>
                  <td className="px-4 py-3 overflow-hidden">
                    <div className="flex items-center gap-1 flex-wrap">
                      {getScopeBadge(pattern)}
                      {pattern.created_by === 'user' && (
                        <span className="px-2 py-0.5 text-xs rounded bg-amber-500/20 text-amber-600 dark:text-amber-400">
                          Manual
                        </span>
                      )}
                      {pattern.source === 'community' && pattern.community_id && (
                        <CommunityBadge
                          communityId={pattern.community_id}
                          version={pattern.version}
                          protected={!!pattern.protected_from_sync}
                        />
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 overflow-hidden">
                    <div className="text-sm font-medium text-foreground truncate">
                      {pattern.sponsor || '(Unknown)'}
                    </div>
                    {pattern.text_template && (
                      <div className="text-xs text-muted-foreground truncate">
                        {pattern.text_template.substring(0, 60)}...
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap">
                    <span className="text-sm text-green-600 dark:text-green-400 font-medium">
                      {pattern.confirmation_count}
                    </span>
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap">
                    <span className={`text-sm font-medium ${
                      pattern.false_positive_count > 0
                        ? 'text-red-600 dark:text-red-400'
                        : 'text-muted-foreground'
                    }`}>
                      {pattern.false_positive_count}
                    </span>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-muted-foreground">
                    {formatDate(pattern.created_at)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-muted-foreground">
                    {formatDate(pattern.last_matched_at)}
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap">
                    {getStatusBadge(pattern.is_active)}
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap text-xs">
                    {pattern.source === 'local' && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); handleSubmitToCommunity(pattern); }}
                        className="px-2 py-1 rounded border border-border hover:bg-accent whitespace-nowrap"
                      >
                        Submit
                      </button>
                    )}
                    {pattern.source === 'community' && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); handleToggleProtect(pattern); }}
                        className="px-2 py-1 rounded border border-border hover:bg-accent whitespace-nowrap"
                      >
                        {pattern.protected_from_sync ? 'Unprotect' : 'Protect'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {paginatedPatterns?.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-muted-foreground">
                    No patterns found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 px-4 py-3 mt-4 bg-card rounded-lg border border-border">
          <div className="text-sm text-muted-foreground">
            Page {page} of {totalPages} ({sortedPatterns?.length || 0} total)
          </div>
          <div className="flex items-center gap-1 sm:gap-2 flex-wrap justify-center">
            <button
              onClick={() => setPage(Math.max(1, page - 1))}
              disabled={page === 1}
              className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
            >
              Previous
            </button>
            {getPageNumbers(page, totalPages).map((p, i) =>
              p === 'ellipsis' ? (
                <span key={`e${i}`} className="px-2 text-muted-foreground">...</span>
              ) : (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className={`px-3 py-1.5 text-sm rounded transition-colors ${
                    p === page
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'
                  }`}
                >
                  {p}
                </button>
              )
            )}
            <button
              onClick={() => setPage(Math.min(totalPages, page + 1))}
              disabled={page === totalPages}
              className="px-3 py-1.5 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      )}

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
    </div>
  );
}

export default PatternsPage;
