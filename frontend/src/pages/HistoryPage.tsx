import { useState, type ReactNode } from 'react';
import { useUrlParams, useUrlSort } from '../hooks/useUrlParams';
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
import { SortHeader } from '../components/SortHeader';
import StatTile from '../components/StatTile';
import StatusBadge from '../components/StatusBadge';
import DownloadsTab from './history/DownloadsTab';
import { btnBase, btnSecondary } from '../components/buttonStyles';
import { selectBase, filterGrid, filterLabel, filterControl } from '../components/fieldStyles';
import { tableLink, tableCard, tableHead, th, tableBody, tableRow, td, tdMuted, tdNum, tdEmpty, rowCard } from '../components/tableStyles';
import { pageHeader, pageTitle } from '../components/pageStyles';

type StatusFilter = 'all' | 'completed' | 'failed';
type SortField = 'processedAt' | 'processingDurationSeconds' | 'adsDetected' | 'reprocessNumber' | 'llmCost' | 'appVersion';
const SORT_FIELDS: SortField[] = ['processedAt', 'processingDurationSeconds', 'adsDetected', 'reprocessNumber', 'llmCost', 'appVersion'];

// Page, filters and sort live in the URL so Back and reload return to the same place.
const PROCESSING_PARAM_DEFAULTS = { page: '1', status: 'all', podcast: '' };
const STATUS_FILTERS: readonly StatusFilter[] = ['all', 'completed', 'failed'];

const elapsedOrDash = (seconds: number | null | undefined) => (seconds == null ? '-' : formatElapsed(seconds));

function ProcessingTab({ tabs }: { tabs: ReactNode }) {
  const { read, readOneOf, readPage, write } = useUrlParams(PROCESSING_PARAM_DEFAULTS, { resetKey: 'page' });
  const statusFilter = readOneOf('status', STATUS_FILTERS, 'all');
  const podcastFilter = read('podcast');
  const page = readPage('page');
  const setPage = (next: number) => write({ page: String(next) });
  const { sortBy: sortField, sortDirection, onSort: handleSort } = useUrlSort(SORT_FIELDS, 'processedAt', 'desc');
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
          <StatTile label="Completed" value={stats.completedCount} tone="success"
            onClick={() => write({ status: statusFilter === 'completed' ? '' : 'completed' })}
            selected={statusFilter === 'completed'}
            title="Filter the list below by completed runs" />
          <StatTile label="Failed" value={stats.failedCount} tone="destructive"
            onClick={() => write({ status: statusFilter === 'failed' ? '' : 'failed' })}
            selected={statusFilter === 'failed'}
            title="Filter the list below by failed runs" />
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
            onChange={(e) => write({ status: e.target.value })}
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
            onChange={(e) => write({ podcast: e.target.value })}
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
        { value: 'processedAt', label: 'Processed' }, { value: 'processingDurationSeconds', label: 'Run time' },
        { value: 'adsDetected', label: 'Ads' }, { value: 'llmCost', label: 'Cost' },
        { value: 'reprocessNumber', label: 'Reprocess number' }, { value: 'appVersion', label: 'Version' },
      ]} />
      {/* Mobile Card Layout */}
      <div className="xl:hidden space-y-3 mb-4">
        {history.length === 0 ? (
          <div className={`${rowCard} p-8 text-center text-muted-foreground`}>
            No processing history yet.
          </div>
        ) : (
          history.map((entry: ProcessingHistoryEntry) => (
            <div key={entry.id} className={rowCard}>
              <div className="flex items-center justify-between mb-2">
                <Link
                  to={`/feeds/${entry.podcastSlug}`}
                  className={`${tableLink} text-sm font-medium truncate max-w-[200px]`}
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
                  className={`${tableLink} text-sm block truncate`}
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
      <div className={`hidden xl:block ${tableCard}`}>
        <div className="overflow-x-auto" role="region" aria-label="Processing history table" tabIndex={0}>
          <table className="w-full">
            <thead className={tableHead}>
              <tr>
                <th className={th}>
                  Podcast
                </th>
                <th className={th}>
                  Episode
                </th>
                <SortHeader field="processedAt" label="Processed" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader align="right" field="processingDurationSeconds" label="Run time"  sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th
                  className={`${th}`}
                  title="Length of the downloaded copy each run processed"
                >
                  Audio length
                </th>
                <SortHeader align="right" field="adsDetected" label="Ads" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader align="right" field="llmCost" label="Cost"  sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader align="right" field="reprocessNumber" label="Reprocess #"  sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <SortHeader field="appVersion" label="Version"  sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                <th className={th}>
                  Status
                </th>
              </tr>
            </thead>
            <tbody className={tableBody}>
              {history.length === 0 ? (
                <tr>
                  <td colSpan={10} className={tdEmpty}>
                    No processing history yet.
                  </td>
                </tr>
              ) : (
                history.map((entry: ProcessingHistoryEntry) => (
                  <tr key={entry.id} className={tableRow}>
                    <td className={td}>
                      <Link
                        to={`/feeds/${entry.podcastSlug}`}
                        className={`${tableLink} text-sm truncate max-w-[150px] block`}
                        title={entry.podcastTitle}
                      >
                        {entry.podcastTitle}
                      </Link>
                    </td>
                    <td className={td}>
                      <Link
                        to={`/feeds/${entry.podcastSlug}/episodes/${entry.episodeId}`}
                        className={`${tableLink} text-sm truncate max-w-[200px] block`}
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
                    <td className={`${tdMuted} whitespace-nowrap`}>
                      {formatDateTime(entry.processedAt)}
                    </td>
                    <td className={`${tdNum}`}>
                      {elapsedOrDash(entry.processingDurationSeconds)}
                    </td>
                    <td className={`${tdMuted}`}>
                      {entry.downloadedDuration != null
                        ? elapsedOrDash(entry.downloadedDuration)
                        : '-'}
                    </td>
                    <td className={`${tdNum} text-foreground`}>
                      {entry.mode === 'chapters' ? '-' : entry.adsDetected}
                    </td>
                    <td className={`${tdNum}`}>
                      {entry.llmCost != null && entry.llmCost > 0 ? `$${entry.llmCost.toFixed(2)}` : '-'}
                    </td>
                    <td className={`${tdNum}`}>
                      {entry.reprocessNumber > 1 ? `#${entry.reprocessNumber}` : '-'}
                    </td>
                    <td className={`${tdMuted}`}>
                      {entry.appVersion || '-'}
                    </td>
                    <td className={td}>
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
