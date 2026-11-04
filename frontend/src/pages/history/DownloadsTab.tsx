import { Fragment, type ReactNode } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { getDownloadHistory, type DownloadQueryParams, type DownloadSortField } from '../../api/history';
import { ALL_SUBSCRIBER_KEYS_QUERY_KEY, feedsQueryOptions, getAllSubscriberKeys } from '../../api/feeds';
import { getSettings } from '../../api/settings';
import type { DownloadHistoryEntry } from '../../api/types';
import { feedDisplayTitle } from '../../utils/feedTitle';
import { sortFeeds } from '../../utils/feedSort';
import { formatDateTime } from '../../utils/format';
import { clientName } from '../../utils/userAgent';
import { useUrlParams, useUrlSearch, useUrlSort } from '../../hooks/useUrlParams';
import DateField from '../../components/DateField';
import { SkeletonRows } from '../../components/Skeleton';
import { Pagination } from '../../components/Pagination';
import { SortHeader } from '../../components/SortHeader';
import DownloadRowActions from './DownloadRowActions';
import SortControls from '../../components/SortControls';
import QueryError from '../../components/QueryError';
import { tableLink, tableCard, tableHead, th, thRight, tableBody, tableRow, td, tdMuted, tdNum, tdEmpty, rowCard } from '../../components/tableStyles';
import { inputBase, selectBase, filterGrid, filterLabel, filterControl } from '../../components/fieldStyles';
import { badgeBase, tint } from '../../components/badgeStyles';
import { pageHeader, pageTitle } from '../../components/pageStyles';

const LIMIT = 20;
const SORT_FIELDS: DownloadSortField[] = ['last_seen_at', 'podcast', 'client_ip', 'location', 'subscriber'];

// Page, filters, search and sort live in the URL so Back and reload return to the same place.
const DOWNLOAD_PARAM_DEFAULTS = {
  page: '1', podcast: '', subscriber: '', since: '', until: '',
};

function BlockedBadge({ row }: { row: DownloadHistoryEntry }) {
  if (!row.userAgentBlocked) return null;
  return <span className={`${badgeBase} ml-2 ${tint.warning}`}>Blocked</span>;
}

function EpisodeLink({ row, className }: { row: DownloadHistoryEntry; className: string }) {
  const title = row.episodeTitle ?? row.episodeId;
  return (
    <Link
      to={`/feeds/${row.podcastSlug}/episodes/${row.episodeId}`}
      className={`${tableLink} text-sm truncate ${className}`}
      title={title}
    >
      {title}
    </Link>
  );
}

function ClientCell({ row, className }: { row: DownloadHistoryEntry; className: string }) {
  return (
    <>
      <span className={className} title={row.userAgent}>{clientName(row.userAgent)}</span>
      <BlockedBadge row={row} />
    </>
  );
}

function ClientIp({ ip }: { ip: string }) {
  // IPv6 may wrap at group boundaries; IPv4 has no break opportunity.
  return ip.split(':').map((part, i) => <Fragment key={i}>{i > 0 && <>:<wbr /></>}{part}</Fragment>);
}

function SubscriberCell({ row }: { row: DownloadHistoryEntry }) {
  if (!row.subscriberLabel) return <>-</>;
  return (
    <>
      {row.subscriberLabel}
      {row.subscriberRevoked && <span className={`${badgeBase} ml-2 ${tint.destructive}`}>Revoked</span>}
    </>
  );
}

function Location({ row, geoEnabled }: { row: DownloadHistoryEntry; geoEnabled: boolean }) {
  if (row.geoStatus === 'private') return <>Local network</>;
  if (row.geoStatus === 'failed' || row.geoStatus === 'unresolvable') return <>Unknown</>;
  if (row.geoStatus === 'ok') {
    return <>{[row.city, row.region, row.country].filter(Boolean).join(', ') || 'Unknown'}</>;
  }
  return (
    <span className="text-muted-foreground/70">{geoEnabled ? 'Pending' : 'lookup off'}</span>
  );
}

interface Props {
  tabs: ReactNode;
}

function DownloadsTab({ tabs }: Props) {
  const { read, readPage, write } = useUrlParams(DOWNLOAD_PARAM_DEFAULTS, { resetKey: 'page' });
  const podcast = read('podcast');
  const subscriber = read('subscriber');
  const since = read('since');
  const until = read('until');
  const page = readPage('page');
  const setPage = (next: number) => write({ page: String(next) });
  const { sortBy: sortField, sortDirection, onSort: handleSort } = useUrlSort(SORT_FIELDS, 'last_seen_at', 'desc');
  const [searchInput, setSearchInput, search] = useUrlSearch('q', { trim: true });

  const params: DownloadQueryParams = {
    page,
    limit: LIMIT,
    sortBy: sortField,
    sortDir: sortDirection,
    ...(podcast && { podcastSlug: podcast }),
    ...(subscriber && { subscriberKeyId: subscriber }),
    ...(since && { since }),
    ...(until && { until }),
    ...(search && { search }),
  };

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['download-history', params],
    queryFn: () => getDownloadHistory(params),
    placeholderData: keepPreviousData,
  });
  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const { data: keys } = useQuery({
    queryKey: ALL_SUBSCRIBER_KEYS_QUERY_KEY,
    queryFn: getAllSubscriberKeys,
  });
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const geoEnabled = settings?.downloadGeoLookupEnabled?.value ?? false;
  const blocked = settings?.jitBlockedUserAgents?.value;

  const rows = data?.downloads ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / LIMIT));


  return (
    <div>
      <div className={pageHeader}>
        <h1 className={pageTitle}>History</h1>
      </div>

      {tabs}
      <div role="tabpanel" id="history-panel-downloads" aria-labelledby="history-tab-downloads" tabIndex={0}>

      <div className={`${filterGrid} sm:grid-cols-2 lg:grid-cols-5 mb-6`}>
        <div className="min-w-0">
          <label htmlFor="downloads-podcast" className={filterLabel}>Podcast</label>
          <select
            id="downloads-podcast"
            value={podcast}
            onChange={(e) => write({ podcast: e.target.value })}
            className={`${filterControl} ${selectBase}`}
          >
            <option value="">All podcasts</option>
            {sortFeeds(feeds ?? [], 'title').map((feed) => (
              <option key={feed.slug} value={feed.slug}>{feedDisplayTitle(feed)}</option>
            ))}
          </select>
        </div>
        <div className="min-w-0">
          <label htmlFor="downloads-subscriber" className={filterLabel}>Subscriber</label>
          <select
            id="downloads-subscriber"
            value={subscriber}
            onChange={(e) => write({ subscriber: e.target.value })}
            className={`${filterControl} ${selectBase}`}
          >
            <option value="">All subscribers</option>
            {(keys ?? []).map((key) => (
              <option key={key.id} value={key.id}>
                {key.label || 'Unlabeled'} ({key.feedTitle}){key.revokedAt ? ' - revoked' : ''}
              </option>
            ))}
          </select>
        </div>
        <div role="group" aria-label="Download date range" className="grid grid-cols-2 gap-2 min-w-0 min-[375px]:col-span-2">
        <DateField
          id="downloads-since"
          label="Since"
          value={since}
          max={until || undefined}
          onChange={(value) => write({ since: value })}
        />
        <DateField
          id="downloads-until"
          label="Until"
          value={until}
          min={since || undefined}
          onChange={(value) => write({ until: value })}
        />
        </div>
        <div className="min-w-0 min-[375px]:col-span-2 lg:col-span-1">
          <label htmlFor="downloads-search" className={filterLabel}>Search</label>
          <input
            id="downloads-search"
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="IP or client"
            className={`${filterControl} ${inputBase}`}
          />
        </div>
      </div>

      <SortControls className="md:hidden" field={sortField} direction={sortDirection} onSort={handleSort} options={[
        { value: 'last_seen_at', label: 'Time' }, { value: 'podcast', label: 'Podcast' },
        { value: 'client_ip', label: 'Client IP' }, { value: 'location', label: 'Location' },
        { value: 'subscriber', label: 'Subscriber' },
      ]} />
      {isLoading && <SkeletonRows count={8} />}
      {error && (
        <QueryError title={data ? 'Download history could not be refreshed. Showing the last loaded rows.' : 'Could not load download history.'} onRetry={() => void refetch()} retrying={isFetching} />
      )}

      {!isLoading && data && (
        <>
          {/* Mobile cards */}
          <div className="md:hidden space-y-3 mb-4">
            {rows.length === 0 ? (
              <div className={`${rowCard} p-8 text-center text-muted-foreground`}>
                No downloads yet.
              </div>
            ) : rows.map((row) => (
              <div key={row.id} className={rowCard}>
                <div className="flex items-start justify-between gap-3 mb-1">
                  <EpisodeLink row={row} className="font-medium min-w-0" />
                  <div className="shrink-0">
                    <DownloadRowActions row={row} blocked={blocked}  />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground truncate mb-3">{row.podcastTitle}</p>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                  <dt className="text-muted-foreground">Requests</dt>
                  <dd className="text-foreground tabular-nums">{row.requestCount}</dd>
                  <dt className="text-muted-foreground">Time</dt>
                  <dd className="text-foreground">{formatDateTime(row.lastSeenAt)}</dd>
                  <dt className="text-muted-foreground">IP</dt>
                  <dd className="text-foreground font-mono"><ClientIp ip={row.clientIp} /></dd>
                  <dt className="text-muted-foreground">Location</dt>
                  <dd className="text-foreground"><Location row={row} geoEnabled={geoEnabled} /></dd>
                  <dt className="text-muted-foreground">Subscriber</dt>
                  <dd className="text-foreground"><SubscriberCell row={row} /></dd>
                  <dt className="text-muted-foreground">Client</dt>
                  <dd className="text-foreground flex items-center min-w-0">
                    <ClientCell row={row} className="truncate" />
                  </dd>
                </dl>
              </div>
            ))}
          </div>

          {/* Desktop table */}
          <div className={`hidden md:block ${tableCard}`}>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className={tableHead}>
                  <tr>
                    <SortHeader field="last_seen_at" label="Time" className="px-4" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                    <th scope="col" className={th}>Episode</th>
                    <SortHeader field="podcast" label="Podcast" className="px-4 hidden lg:table-cell" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                    <SortHeader field="client_ip" label="Client IP" className="px-4" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                    <SortHeader field="location" label="Location" className="px-4 hidden xl:table-cell" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                    <SortHeader field="subscriber" label="Subscriber" className="px-4 hidden lg:table-cell" sortField={sortField} sortDirection={sortDirection} onSort={handleSort} />
                    <th scope="col" className={`${th} hidden xl:table-cell`}>Client</th>
                    <th scope="col" className={thRight}>Requests</th>
                    <th scope="col" className={th}><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className={tableBody}>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={9} className={tdEmpty}>
                        No downloads yet.
                      </td>
                    </tr>
                  ) : rows.map((row) => (
                    <tr key={row.id} className={tableRow}>
                      <td className={`${tdMuted} whitespace-nowrap`} title={`First seen ${formatDateTime(row.firstSeenAt)}`}>
                        {formatDateTime(row.lastSeenAt)}
                      </td>
                      <td className={td}>
                        <EpisodeLink row={row} className="max-w-[150px] block" />
                      </td>
                      <td className={`${td} hidden lg:table-cell`}>
                        <Link
                          to={`/feeds/${row.podcastSlug}`}
                          className={`${tableLink} text-sm truncate max-w-[110px] block`}
                          title={row.podcastTitle}
                        >
                          {row.podcastTitle}
                        </Link>
                      </td>
                      <td className={`${td} text-foreground font-mono min-w-[9rem]`}><ClientIp ip={row.clientIp} /></td>
                      <td className={`${tdMuted} hidden xl:table-cell`}>
                        <Location row={row} geoEnabled={geoEnabled} />
                      </td>
                      <td className={`${tdMuted} hidden lg:table-cell`}>
                        <SubscriberCell row={row} />
                      </td>
                      <td className={`${tdMuted} hidden xl:table-cell`}>
                        <ClientCell row={row} className="block truncate max-w-[140px]" />
                      </td>
                      <td className={`${tdNum} text-foreground`}>{row.requestCount}</td>
                      <td className={`${td} text-right`}>
                        <DownloadRowActions row={row} blocked={blocked} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <Pagination page={page} totalPages={totalPages} total={total} onPage={setPage} />
        </>
      )}
      </div>
    </div>
  );
}

export default DownloadsTab;
