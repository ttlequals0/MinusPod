import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  getProcessingHistory,
  getProcessingHistoryStats,
  exportProcessingHistory,
  downloadBlob,
  HistoryQueryParams,
} from '../api/history';
import { getFeeds } from '../api/feeds';
import { ProcessingHistoryEntry } from '../api/types';
import LoadingSpinner from '../components/LoadingSpinner';

type StatusFilter = 'all' | 'completed' | 'failed';
type SortField = 'processedAt' | 'processingDurationSeconds' | 'adsDetected' | 'reprocessNumber';

function HistoryPage() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [podcastFilter, setPodcastFilter] = useState<string>('');
  const [page, setPage] = useState(1);
  const [sortField, setSortField] = useState<SortField>('processedAt');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [exporting, setExporting] = useState<'csv' | 'json' | null>(null);
  const limit = 20;

  // Build query params
  const queryParams: HistoryQueryParams = {
    page,
    limit,
    sortBy: sortField === 'processedAt' ? 'processed_at' :
            sortField === 'processingDurationSeconds' ? 'processing_duration_seconds' :
            'ads_detected',
    sortDir: sortDirection,
  };
  if (statusFilter !== 'all') {
    queryParams.status = statusFilter;
  }
  if (podcastFilter) {
    queryParams.podcastSlug = podcastFilter;
  }

  const { data: historyData, isLoading, error } = useQuery({
    queryKey: ['history', queryParams],
    queryFn: () => getProcessingHistory(queryParams),
  });

  const { data: stats } = useQuery({
    queryKey: ['history-stats'],
    queryFn: getProcessingHistoryStats,
  });

  const { data: feeds } = useQuery({
    queryKey: ['feeds'],
    queryFn: getFeeds,
  });

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
    setPage(1);
  };

  const handleExport = async (format: 'csv' | 'json') => {
    setExporting(format);
    try {
      const blob = await exportProcessingHistory(format);
      const timestamp = new Date().toISOString().split('T')[0];
      downloadBlob(blob, `processing-history-${timestamp}.${format}`);
    } catch (err) {
      console.error('Export failed:', err);
    } finally {
      setExporting(null);
    }
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const formatDuration = (seconds: number | null) => {
    if (seconds === null || seconds === undefined) {
      return '-';
    }
    if (seconds < 60) {
      return `${seconds.toFixed(1)}s`;
    }
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
  };

  const SortHeader = ({ field, label, className = '' }: { field: SortField; label: string; className?: string }) => (
    <th
      className={`px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider cursor-pointer hover:bg-accent/50 ${className}`}
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
        <p className="text-destructive">Failed to load processing history</p>
        <p className="text-sm text-muted-foreground mt-2">{(error as Error).message}</p>
      </div>
    );
  }

  const history = historyData?.history || [];
  const totalPages = historyData?.totalPages || 1;

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4 mb-6">
        <h1 className="text-2xl font-bold text-foreground">Processing History</h1>
        <div className="flex gap-2">
          <button
            onClick={() => handleExport('csv')}
            disabled={exporting !== null}
            className="px-3 py-2 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          >
            {exporting === 'csv' ? 'Exporting...' : 'Export CSV'}
          </button>
          <button
            onClick={() => handleExport('json')}
            disabled={exporting !== null}
            className="px-3 py-2 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
          >
            {exporting === 'json' ? 'Exporting...' : 'Export JSON'}
          </button>
        </div>
      </div>

      {/* Stats Summary */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-2xl font-bold text-foreground">{stats.totalProcessed}</div>
            <div className="text-sm text-muted-foreground">Total Processed</div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-2xl font-bold text-green-600 dark:text-green-400">{stats.completedCount}</div>
            <div className="text-sm text-muted-foreground">Completed</div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-2xl font-bold text-red-600 dark:text-red-400">{stats.failedCount}</div>
            <div className="text-sm text-muted-foreground">Failed</div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="text-2xl font-bold text-foreground">{stats.totalAdsDetected}</div>
            <div className="text-sm text-muted-foreground">Total Ads Detected</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4 mb-6">
        <div className="flex gap-2">
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value as StatusFilter);
              setPage(1);
            }}
            className="px-3 py-2 rounded bg-secondary text-secondary-foreground border border-border text-sm"
          >
            <option value="all">All Status</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
          <select
            value={podcastFilter}
            onChange={(e) => {
              setPodcastFilter(e.target.value);
              setPage(1);
            }}
            className="px-3 py-2 rounded bg-secondary text-secondary-foreground border border-border text-sm"
          >
            <option value="">All Podcasts</option>
            {feeds?.map((feed) => (
              <option key={feed.slug} value={feed.slug}>
                {feed.title}
              </option>
            ))}
          </select>
        </div>
        {stats && (
          <div className="text-sm text-muted-foreground self-center">
            Avg processing time: {formatDuration(stats.avgProcessingTime)}
          </div>
        )}
      </div>

      {/* Table */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Podcast
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Episode
                </th>
                <SortHeader field="processedAt" label="Processed" />
                <SortHeader field="processingDurationSeconds" label="Duration" className="hidden sm:table-cell" />
                <SortHeader field="adsDetected" label="Ads" />
                <SortHeader field="reprocessNumber" label="Reprocess #" className="hidden sm:table-cell" />
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {history.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">
                    No processing history found
                  </td>
                </tr>
              ) : (
                history.map((entry: ProcessingHistoryEntry) => (
                  <tr key={entry.id} className="hover:bg-muted/50">
                    <td className="px-4 py-3">
                      <Link
                        to={`/feeds/${entry.podcastSlug}`}
                        className="text-primary hover:underline text-sm truncate max-w-[150px] block"
                        title={entry.podcastTitle}
                      >
                        {entry.podcastTitle}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <Link
                        to={`/feeds/${entry.podcastSlug}/episodes/${entry.episodeId}`}
                        className="text-primary hover:underline text-sm truncate max-w-[200px] block"
                        title={entry.episodeTitle}
                      >
                        {entry.episodeTitle}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
                      {formatDate(entry.processedAt)}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground hidden sm:table-cell">
                      {formatDuration(entry.processingDurationSeconds)}
                    </td>
                    <td className="px-4 py-3 text-sm text-foreground">
                      {entry.adsDetected}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground hidden sm:table-cell">
                      {entry.reprocessNumber > 1 ? `#${entry.reprocessNumber}` : '-'}
                    </td>
                    <td className="px-4 py-3">
                      {entry.status === 'completed' ? (
                        <span className="px-2 py-0.5 text-xs rounded bg-green-500/20 text-green-600 dark:text-green-400">
                          Completed
                        </span>
                      ) : (
                        <span
                          className="px-2 py-0.5 text-xs rounded bg-red-500/20 text-red-600 dark:text-red-400 cursor-help"
                          title={entry.errorMessage || 'Processing failed'}
                        >
                          Failed
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-border bg-muted/30">
            <div className="text-sm text-muted-foreground">
              Page {page} of {totalPages} ({historyData?.total || 0} total)
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page === 1}
                className="px-3 py-1 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
              >
                Previous
              </button>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page === totalPages}
                className="px-3 py-1 text-sm rounded bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50 transition-colors"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default HistoryPage;
