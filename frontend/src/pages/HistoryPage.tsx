import { useState, type ReactNode } from 'react';
import PageTabs from '../components/PageTabs';
import SortControls from '../components/SortControls';
import QueryError from '../components/QueryError';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router';
import {
  getProcessingHistory,
  getProcessingHistoryStats,
  exportProcessingHistory,
  downloadBlob,
  HistoryQueryParams,
} from '../api/history';
import { feedsQueryOptions } from '../api/feeds';
import { feedDisplayTitle } from '../utils/feedTitle';
import { formatDateTime, formatElapsed } from '../utils/format';
import { ProcessingHistoryEntry } from '../api/types';
import { SkeletonRows } from '../components/Skeleton';
import { Pagination } from '../components/Pagination';
import { SortHeader, useSortState } from '../components/SortHeader';
import StatTile from '../components/StatTile';
import StatusBadge from '../components/StatusBadge';
import DownloadsTab from './history/DownloadsTab';
import { btnBase, btnSecondary } from '../components/buttonStyles';
import { focusRing, selectBase, filterGrid, filterLabel, filterControl } from '../components/fieldStyles';
import { pageHeader, pageTitle } from '../components/pageStyles';

type StatusFilter = 'all' | 'completed' | 'failed';
type SortField = 'processedAt' | 'processingDurationSeconds' | 'adsDetected' | 'reprocessNumber' | 'llmCost' | 'appVersion';

const FILTER_TILE_CLASS = 'h-full w-full rounded-lg cursor-pointer transition-colors hover:border-primary/50';

const elapsedOrDash = (seconds: number | null | undefined) => (seconds == null ? '-' : formatElapsed(seconds));

function ProcessingTab({ tabs }: { tabs: ReactNode }) {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [podcastFilter, setPodcastFilter] = useState<string>('');
  const [page, setPage] = useState(1);
  const { sortField, sortDirection, handleSort } =
    useSortState<SortField>('processedAt', 'desc', () => setPage(1));
  const [exporting, setExporting] = useState<'csv' | 'json' | null>(null);
  const [exportError, setExportError] = useState('');
  const limit = 20;

  // Build query params
  const queryParams: HistoryQueryParams = {
    page,
    limit,
    sortBy: sortField === 'processedAt' ? 'processed_at' :
            sortField === 'processingDurationSeconds' ? 'processing_duration_seconds' :
            sortField === 'reprocessNumber' ? 'reprocess_number' :
            sortField === 'llmCost' ? 'llm_cost' :
            sortField === 'appVersion' ? 'app_version' :
            'ads_detected',
    sortDir: sortDirection,
  };
  if (statusFilter !== 'all') {
    queryParams.status = statusFilter;
  }
  if (podcastFilter) {
    queryParams.podcastSlug = podcastFilter;
  }

  const { data: historyData, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['history', queryParams],
    queryFn: () => getProcessingHistory(queryParams),
  });

  const { data: stats } = useQuery({
    queryKey: ['history-stats'],
    queryFn: getProcessingHistoryStats,
  });

  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });

  const handleExport = async (format: 'csv' | 'json') => {
    setExporting(format);
    setExportError('');
    try {
      const blob = await exportProcessingHistory(format);
      const timestamp = new Date().toISOString().split('T')[0];
      downloadBlob(blob, `processing-history-${timestamp}.${format}`);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Could not export history. Try again.');
    } finally {
      setExporting(null);
    }
  };

  if (isLoading) {
    return <div><div className={pageHeader}><h1 className={pageTitle}>History</h1></div>{tabs}<div role="tabpanel" id="history-panel-processing" aria-labelledby="history-tab-processing"><SkeletonRows count={8} /></div></div>;
  }

  if (error && !historyData) {
    return <div>
      <div className={pageHeader}><h1 className={pageTitle}>History</h1></div>{tabs}
      <div role="tabpanel" id="history-panel-processing" aria-labelledby="history-tab-processing">
        <QueryError title="Could not load processing history." onRetry={() => void refetch()} retrying={isFetching} />
      </div>
    </div>;
  }

  const history = historyData?.history || [];
  const totalPages = historyData?.totalPages || 1;

  return (
    <div>
      <div className={pageHeader}>
        <h1 className={pageTitle}>History</h1>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => handleExport('csv')}
            disabled={exporting !== null}
            className={`${btnBase} ${btnSecondary}`}
          >
            {exporting === 'csv' ? 'Exporting...' : 'Export CSV'}
          </button>
          <button
            onClick={() => handleExport('json')}
            disabled={exporting !== null}
            className={`${btnBase} ${btnSecondary}`}
          >
            {exporting === 'json' ? 'Exporting...' : 'Export JSON'}
          </button>
        </div>
      </div>

      {tabs}
      <div role="tabpanel" id="history-panel-processing" aria-labelledby="history-tab-processing" tabIndex={0}>
      {error && <div className="mb-4"><QueryError title="History could not be refreshed. Showing the last loaded rows." onRetry={() => void refetch()} retrying={isFetching} /></div>}
      {exportError && <p role="alert" className="text-destructive mb-4">{exportError}</p>}

      {/* Stats Summary */}
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
          <StatTile label="Total processed" value={stats.totalProcessed} />
          <button
            type="button"
            onClick={() => { setStatusFilter((f) => (f === 'completed' ? 'all' : 'completed')); setPage(1); }}
            aria-pressed={statusFilter === 'completed'}
            title="Filter the list below by completed runs"
            className={`block h-full min-w-0 rounded-lg ${focusRing}`}
          >
            <StatTile label="Completed" value={stats.completedCount} tone="success"
              className={`${FILTER_TILE_CLASS} ${statusFilter === 'completed' ? 'border-primary bg-primary/5' : 'border-border'}`} />
          </button>
          <button
            type="button"
            onClick={() => { setStatusFilter((f) => (f === 'failed' ? 'all' : 'failed')); setPage(1); }}
            aria-pressed={statusFilter === 'failed'}
            title="Filter the list below by failed runs"
            className={`block h-full min-w-0 rounded-lg ${focusRing}`}
          >
            <StatTile label="Failed" value={stats.failedCount} tone="destructive"
              className={`${FILTER_TILE_CLASS} ${statusFilter === 'failed' ? 'border-primary bg-primary/5' : 'border-border'}`} />
          </button>
          <StatTile label="Total ads detected" value={stats.totalAdsDetected} />
          <StatTile label="Total cost" value={stats.totalLlmCost != null ? `$${stats.totalLlmCost.toFixed(2)}` : '-'} />
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4 mb-6">
        <div className={`${filterGrid} w-full sm:flex-1`}>
          <div className="min-w-0">
          <label htmlFor="history-status" className={filterLabel}>Status</label>
          <select
            id="history-status"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value as StatusFilter);
              setPage(1);
            }}
            className={`${filterControl} ${selectBase}`}
          >
            <option value="all">All statuses</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
          </div>
          <div className="min-w-0">
          <label htmlFor="history-podcast" className={filterLabel}>Podcast</label>
          <select
            id="history-podcast"
            value={podcastFilter}
            onChange={(e) => {
              setPodcastFilter(e.target.value);
              setPage(1);
            }}
            className={`${filterControl} ${selectBase}`}
          >
            <option value="">All podcasts</option>
            {[...(feeds ?? [])]
              .sort((a, b) => feedDisplayTitle(a).localeCompare(feedDisplayTitle(b)))
              .map((feed) => (
                <option key={feed.slug} value={feed.slug}>
                  {feedDisplayTitle(feed)}
                </option>
              ))}
          </select>
          </div>
        </div>
        {stats && (
          <div className="text-sm text-muted-foreground self-center">
            Avg processing time: {elapsedOrDash(stats.avgProcessingTimeSeconds)}
          </div>
        )}
      </div>

      <SortControls className="xl:hidden" field={sortField} direction={sortDirection} onSort={handleSort} options={[
        { value: 'processedAt', label: 'Processed' }, { value: 'processingDurationSeconds', label: 'Duration' },
        { value: 'adsDetected', label: 'Ads' }, { value: 'llmCost', label: 'Cost' },
        { value: 'reprocessNumber', label: 'Reprocess number' }, { value: 'appVersion', label: 'Version' },
      ]} />
      {/* Mobile Card Layout */}
      <div className="xl:hidden space-y-3 mb-4">
        {history.length === 0 ? (
          <div className="bg-card rounded-lg border border-border p-8 text-center text-muted-foreground">
            No processing history yet.
          </div>
        ) : (
          history.map((entry: ProcessingHistoryEntry) => (
            <div key={entry.id} className="bg-card rounded-lg border border-border p-4">
              <div className="flex items-center justify-between mb-2">
                <Link
                  to={`/feeds/${entry.podcastSlug}`}
                  className={`text-primary hover:underline text-sm font-medium truncate max-w-[200px] ${focusRing}`}
                  title={entry.podcastTitle}
                >
                  {entry.podcastTitle}
                </Link>
                {entry.status === 'completed' ? (
                  <StatusBadge status="completed" />
                ) : (
                  <span title={entry.errorMessage || 'Processing failed'} className="cursor-help"><StatusBadge status="failed" /></span>
                )}
              </div>
              <div className="mb-3">
                <Link
                  to={`/feeds/${entry.podcastSlug}/episodes/${entry.episodeId}`}
                  className={`text-primary hover:underline text-sm block truncate ${focusRing}`}
                  title={entry.episodeTitle}
                >
                  {entry.episodeTitle}
                </Link>
                {entry.mode === 'chapters' && (
                  <p className="text-xs text-muted-foreground">Chapter regeneration</p>
                )}
                {entry.status !== 'completed' && entry.errorMessage && (
                  <p
                    className="text-xs text-destructive break-words mt-1"
                    title={entry.errorMessage}
                  >
                    {entry.errorMessage}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span>{formatDateTime(entry.processedAt)}</span>
                <span>{elapsedOrDash(entry.processingDurationSeconds)}</span>
                {entry.downloadedDuration != null && (
                  <span title="Length of the downloaded copy this run processed">
                    Audio: {elapsedOrDash(entry.downloadedDuration)}
                  </span>
                )}
                <span>Ads: {entry.mode === 'chapters' ? '-' : entry.adsDetected}</span>
                {entry.llmCost != null && entry.llmCost > 0 && <span>${entry.llmCost.toFixed(2)}</span>}
                {entry.reprocessNumber > 1 && <span>#{entry.reprocessNumber}</span>}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Desktop Table Layout */}
      <div className="hidden xl:block bg-card border border-border rounded-lg overflow-hidden">
        <div className="overflow-x-auto" role="region" aria-label="Processing history table" tabIndex={0}>
          <table className="w-full">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Podcast
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Episode
                </th>
                <SortHeader field="processedAt" label="Processed" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader align="right" field="processingDurationSeconds" label="Duration" className="px-4 hidden md:table-cell" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th
                  className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider hidden lg:table-cell"
                  title="Length of the downloaded copy each run processed"
                >
                  Audio
                </th>
                <SortHeader align="right" field="adsDetected" label="Ads" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader align="right" field="llmCost" label="Cost" className="px-4 hidden md:table-cell" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader align="right" field="reprocessNumber" label="Reprocess #" className="px-4 hidden md:table-cell" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="appVersion" label="Version" className="px-4 hidden md:table-cell" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {history.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-4 py-8 text-center text-muted-foreground">
                    No processing history yet.
                  </td>
                </tr>
              ) : (
                history.map((entry: ProcessingHistoryEntry) => (
                  <tr key={entry.id} className="hover:bg-muted/50">
                    <td className="px-4 py-3">
                      <Link
                        to={`/feeds/${entry.podcastSlug}`}
                        className={`text-primary hover:underline text-sm truncate max-w-[150px] block ${focusRing}`}
                        title={entry.podcastTitle}
                      >
                        {entry.podcastTitle}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <Link
                        to={`/feeds/${entry.podcastSlug}/episodes/${entry.episodeId}`}
                        className={`text-primary hover:underline text-sm truncate max-w-[200px] block ${focusRing}`}
                        title={entry.episodeTitle}
                      >
                        {entry.episodeTitle}
                      </Link>
                      {entry.mode === 'chapters' && (
                        <span className="block text-xs text-muted-foreground">Chapter regeneration</span>
                      )}
                      {entry.status !== 'completed' && entry.errorMessage && (
                        <span
                          className="block text-xs text-destructive break-words max-w-[200px] cursor-help mt-1"
                          title={entry.errorMessage}
                        >
                          {entry.errorMessage}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
                      {formatDateTime(entry.processedAt)}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground hidden md:table-cell">
                      {elapsedOrDash(entry.processingDurationSeconds)}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground hidden lg:table-cell">
                      {entry.downloadedDuration != null
                        ? elapsedOrDash(entry.downloadedDuration)
                        : '-'}
                    </td>
                    <td className="px-4 py-3 text-sm text-foreground">
                      {entry.mode === 'chapters' ? '-' : entry.adsDetected}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground hidden md:table-cell">
                      {entry.llmCost != null && entry.llmCost > 0 ? `$${entry.llmCost.toFixed(2)}` : '-'}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground hidden md:table-cell">
                      {entry.reprocessNumber > 1 ? `#${entry.reprocessNumber}` : '-'}
                    </td>
                    <td className="px-4 py-3 text-sm text-muted-foreground hidden md:table-cell">
                      {entry.appVersion || '-'}
                    </td>
                    <td className="px-4 py-3">
                      {entry.status === 'completed' ? (
                  <StatusBadge status="completed" />
                ) : (
                  <span title={entry.errorMessage || 'Processing failed'} className="cursor-help"><StatusBadge status="failed" /></span>
                )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

      </div>

      {/* Pagination - outside desktop/mobile divs for visibility on all screens */}
      <Pagination page={page} totalPages={totalPages} total={historyData?.total || 0} onPage={setPage} />
      </div>
    </div>
  );
}

const TABS = [
  ['processing', 'Processing'],
  ['downloads', 'Downloads'],
] as const;

function HistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get('tab') === 'downloads' ? 'downloads' : 'processing';

  const tabs = <PageTabs id="history" label="History sections" active={activeTab}
    tabs={TABS.map(([value, label]) => ({ value, label }))}
    onChange={key => setSearchParams(key === 'processing' ? {} : { tab: key })} />;

  return activeTab === 'downloads' ? <DownloadsTab tabs={tabs} /> : <ProcessingTab tabs={tabs} />;
}

export default HistoryPage;
