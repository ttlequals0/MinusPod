import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getPatterns, AdPattern } from '../api/patterns';
import PatternDetailModal from '../components/PatternDetailModal';
import LoadingSpinner from '../components/LoadingSpinner';

type ScopeFilter = 'all' | 'global' | 'network' | 'podcast';

function PatternsPage() {
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [showInactive, setShowInactive] = useState(false);
  const [selectedPattern, setSelectedPattern] = useState<AdPattern | null>(null);
  const [sortField, setSortField] = useState<keyof AdPattern>('confirmation_count');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');

  const { data: patterns, isLoading, error, refetch } = useQuery({
    queryKey: ['patterns', scopeFilter, showInactive],
    queryFn: () => getPatterns({
      scope: scopeFilter === 'all' ? undefined : scopeFilter,
      active: showInactive ? undefined : true,
    }),
  });

  const handleSort = (field: keyof AdPattern) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  const filteredPatterns = patterns?.filter(pattern => {
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      return (
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

    let comparison = 0;
    if (typeof aVal === 'string' && typeof bVal === 'string') {
      comparison = aVal.localeCompare(bVal);
    } else if (typeof aVal === 'number' && typeof bVal === 'number') {
      comparison = aVal - bVal;
    } else {
      comparison = String(aVal).localeCompare(String(bVal));
    }

    return sortDirection === 'asc' ? comparison : -comparison;
  });

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
      return <span className="px-2 py-0.5 text-xs rounded bg-green-500/20 text-green-600 dark:text-green-400">Podcast</span>;
    }
    return null;
  };

  const SortHeader = ({ field, label }: { field: keyof AdPattern; label: string }) => (
    <th
      className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider cursor-pointer hover:bg-accent/50"
      onClick={() => handleSort(field)}
    >
      <div className="flex items-center gap-1">
        {label}
        {sortField === field && (
          <span>{sortDirection === 'asc' ? '\u2191' : '\u2193'}</span>
        )}
      </div>
    </th>
  );

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
        <div className="text-sm text-muted-foreground">
          {sortedPatterns?.length || 0} patterns
        </div>
      </div>

      {/* Filters */}
      <div className="bg-card rounded-lg border border-border p-4 mb-6">
        <div className="flex flex-wrap gap-4 items-center">
          {/* Scope filter */}
          <div className="flex items-center gap-2">
            <label className="text-sm text-muted-foreground">Scope:</label>
            <select
              value={scopeFilter}
              onChange={(e) => setScopeFilter(e.target.value as ScopeFilter)}
              className="px-3 py-1.5 text-sm bg-secondary border border-border rounded"
            >
              <option value="all">All</option>
              <option value="global">Global</option>
              <option value="network">Network</option>
              <option value="podcast">Podcast</option>
            </select>
          </div>

          {/* Search */}
          <div className="flex-1 min-w-[200px]">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by sponsor, text, network..."
              className="w-full px-3 py-1.5 text-sm bg-secondary border border-border rounded"
            />
          </div>

          {/* Show inactive toggle */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="rounded"
            />
            <span className="text-sm text-muted-foreground">Show inactive</span>
          </label>
        </div>
      </div>

      {/* Table */}
      <div className="bg-card rounded-lg border border-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-border">
            <thead className="bg-muted/50">
              <tr>
                <SortHeader field="scope" label="Scope" />
                <SortHeader field="sponsor" label="Sponsor" />
                <SortHeader field="confirmation_count" label="Confirmed" />
                <SortHeader field="false_positive_count" label="False Pos." />
                <SortHeader field="last_matched_at" label="Last Matched" />
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sortedPatterns?.map((pattern) => (
                <tr
                  key={pattern.id}
                  className="hover:bg-accent/50 cursor-pointer transition-colors"
                  onClick={() => setSelectedPattern(pattern)}
                >
                  <td className="px-4 py-3 whitespace-nowrap">
                    {getScopeBadge(pattern)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="text-sm font-medium text-foreground">
                      {pattern.sponsor || '(Unknown)'}
                    </div>
                    {pattern.text_template && (
                      <div className="text-xs text-muted-foreground truncate max-w-xs">
                        {pattern.text_template.substring(0, 60)}...
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span className="text-sm text-green-600 dark:text-green-400 font-medium">
                      {pattern.confirmation_count}
                    </span>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span className={`text-sm font-medium ${
                      pattern.false_positive_count > 0
                        ? 'text-red-600 dark:text-red-400'
                        : 'text-muted-foreground'
                    }`}>
                      {pattern.false_positive_count}
                    </span>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-sm text-muted-foreground">
                    {formatDate(pattern.last_matched_at)}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {pattern.is_active ? (
                      <span className="px-2 py-0.5 text-xs rounded bg-green-500/20 text-green-600 dark:text-green-400">
                        Active
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 text-xs rounded bg-red-500/20 text-red-600 dark:text-red-400">
                        Inactive
                      </span>
                    )}
                  </td>
                </tr>
              ))}
              {sortedPatterns?.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                    No patterns found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

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
