import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { APPROVAL_INVALIDATE_KEYS, approveAwaitingEpisodes, getAwaitingApproval, getHeldEpisodes } from '../../api/settings';
import type { ApprovalEpisodeList, ApprovalEpisodeRow } from '../../api/settings';
import { getErrorMessage } from '../../api/client';
import { useUrlParams } from '../../hooks/useUrlParams';
import Checkbox from '../../components/Checkbox';
import LoadingSpinner from '../../components/LoadingSpinner';
import { Pagination } from '../../components/Pagination';
import QueryError from '../../components/QueryError';
import { btnBase, btnPrimary, touchTarget } from '../../components/buttonStyles';
import { tableBody, tableCard, tableHead, tableLink, tableRow, td, tdMuted, tdNum, th, thRight } from '../../components/tableStyles';
import { badgeBase, tint } from '../../components/badgeStyles';
import { cardTitle } from '../../components/pageStyles';
import { formatTimeAgo } from '../../utils/format';
import { DemoDisabled } from '../../components/DemoDisabled';

const REVIEW_PAGE_SIZE = 25;

const rowKey = (row: ApprovalEpisodeRow) => `${row.slug}:${row.episodeId}`;
const episodePath = (row: ApprovalEpisodeRow) => `/feeds/${row.slug}/episodes/${row.episodeId}`;
const emptyBox = 'rounded-lg bg-secondary/50 p-4 text-sm text-muted-foreground';

// Each section keeps its own page in the URL so Back and reload return to the same place.
const REVIEW_PARAM_DEFAULTS = { awaitingPage: '1', heldPage: '1' };

function usePagedQuery(
  key: string,
  param: keyof typeof REVIEW_PARAM_DEFAULTS,
  fetcher: (params: { limit: number; offset: number }) => Promise<ApprovalEpisodeList>,
) {
  const { readPage, write } = useUrlParams(REVIEW_PARAM_DEFAULTS);
  // Last page count the server reported; clamps the URL page when a section's last page empties.
  const [knownPages, setKnownPages] = useState<number | undefined>(undefined);
  const page = readPage(param, knownPages);
  const setPage = (next: number) => write({ [param]: String(next) });
  const query = useQuery({
    queryKey: [key, page],
    queryFn: () => fetcher({ limit: REVIEW_PAGE_SIZE, offset: (page - 1) * REVIEW_PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const pages = query.isPlaceholderData || !query.data ? undefined : Math.max(1, Math.ceil(query.data.count / REVIEW_PAGE_SIZE));
  if (pages !== undefined && pages !== knownPages) setKnownPages(pages);
  return { query, page, setPage };
}

interface ReviewColumn {
  label: string;
  srLabel?: string;
  align?: 'right';
  cell?: string;
  render: (row: ApprovalEpisodeRow) => ReactNode;
}

const episodeCell = (to: (row: ApprovalEpisodeRow) => string): ReviewColumn => ({
  label: 'Episode',
  render: (row) => <Link to={to(row)} className={`${tableLink} block truncate max-w-[320px]`} title={row.title}>{row.title}</Link>,
});
const podcastCell: ReviewColumn = {
  label: 'Podcast',
  cell: tdMuted,
  render: (row) => <span className="block truncate max-w-[220px]" title={row.podcast}>{row.podcast}</span>,
};
const adsCell: ReviewColumn = { label: 'Ads', align: 'right', cell: tdNum, render: (row) => row.adCount };
const heldCell: ReviewColumn = {
  label: 'Held',
  cell: tdMuted,
  render: (row) => row.pendingReviewCount > 0
    ? <span className={`${badgeBase} ${tint.warning}`}>{row.pendingReviewCount} held</span>
    : '-',
};
const waitingCell: ReviewColumn = {
  label: 'Waiting',
  cell: tdMuted,
  render: (row) => row.pendingSince ? formatTimeAgo(row.pendingSince) : '-',
};
const selectColumn = (selectBox: (row: ApprovalEpisodeRow) => ReactNode): ReviewColumn => ({
  label: '',
  srLabel: 'Select',
  render: selectBox,
});

interface ReviewSectionProps {
  id: string;
  title: string;
  blurb: string;
  query: UseQueryResult<ApprovalEpisodeList>;
  emptyText: string;
  errorTitle: string;
  rows: ApprovalEpisodeRow[];
  columns: ReviewColumn[];
  renderRow: (row: ApprovalEpisodeRow) => ReactNode;
  page: number;
  onPage: (page: number) => void;
  toolbar?: ReactNode;
}

function ReviewSection({ id, title, blurb, query, emptyText, errorTitle, rows, columns, renderRow, page, onPage, toolbar }: ReviewSectionProps) {
  const total = query.data?.count ?? 0;
  return (
    <section className="bg-card rounded-lg border border-border p-4 sm:p-6" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className={cardTitle}>{title}</h2>
      <p className="text-sm text-muted-foreground mt-0.5 mb-4">{blurb}</p>
      {query.isError && !query.data ? (
        <QueryError title={errorTitle} onRetry={() => void query.refetch()} retrying={query.isFetching} />
      ) : query.isPending ? <LoadingSpinner size="sm" /> : rows.length === 0 ? (
        <p className={emptyBox}>{emptyText}</p>
      ) : (
        <>
          {toolbar}
          <div className={`hidden xl:block ${tableCard}`}>
            <table className="w-full">
              <thead className={tableHead}>
                <tr>
                  {columns.map((col) => (
                    <th key={col.label || col.srLabel} scope="col" className={col.align === 'right' ? thRight : th}>
                      {col.srLabel ? <span className="sr-only">{col.srLabel}</span> : col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className={tableBody}>
                {rows.map((row) => (
                  <tr key={rowKey(row)} className={tableRow}>
                    {columns.map((col) => (
                      <td key={col.label || col.srLabel} className={col.cell ?? td}>{col.render(row)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="space-y-2 xl:hidden">
            {rows.map((row) => (
              <li key={rowKey(row)} className="rounded-lg bg-secondary/50 p-3 flex items-start gap-3">
                {renderRow(row)}
              </li>
            ))}
          </ul>
          <Pagination page={page} totalPages={Math.max(1, Math.ceil(total / REVIEW_PAGE_SIZE))} total={total} onPage={onPage} />
        </>
      )}
      {query.isError && query.data && <p role="alert" className="text-sm text-destructive mt-3">Could not refresh the list. Showing the last result.</p>}
    </section>
  );
}

function rowBody(row: ApprovalEpisodeRow, to: string, extra?: ReactNode) {
  return (
    <div className="min-w-0 flex-1">
      <Link to={to} className={`block font-medium break-words ${tableLink}`}>
        {row.title}
      </Link>
      <p className="text-sm text-muted-foreground flex flex-wrap gap-x-3 gap-y-1">
        <span className="truncate max-w-full">{row.podcast}</span>
        {extra}
        <span className="whitespace-nowrap">{row.adCount} {row.adCount === 1 ? 'ad' : 'ads'}</span>
        {row.pendingReviewCount > 0 && (
          <span className={`${badgeBase} ${tint.warning}`}>{row.pendingReviewCount} held</span>
        )}
      </p>
    </div>
  );
}

function AwaitingApproval() {
  const queryClient = useQueryClient();
  const { query: awaiting, page, setPage } = usePagedQuery('awaiting-approval', 'awaitingPage', getAwaitingApproval);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const approve = useMutation({
    mutationFn: (rows: ApprovalEpisodeRow[]) =>
      approveAwaitingEpisodes(rows.map(({ slug, episodeId }) => ({ slug, episodeId }))),
    onMutate: () => setMessage(null),
    onSuccess: (result) => {
      setSelected(new Set());
      if (result.skipped > 0) {
        setMessage(`${result.skipped} ${result.skipped === 1 ? 'episode was' : 'episodes were'} skipped because ${result.skipped === 1 ? 'it was' : 'they were'} no longer awaiting approval.`);
      }
      APPROVAL_INVALIDATE_KEYS.forEach((key) => void queryClient.invalidateQueries({ queryKey: [key] }));
    },
    onError: (error) => setMessage(getErrorMessage(error, 'Could not approve the selected episodes.')),
  });

  const rows = awaiting.data?.episodes ?? [];
  const chosen = rows.filter((row) => selected.has(rowKey(row)));
  const allChosen = rows.length > 0 && chosen.length === rows.length;
  const toggle = (row: ApprovalEpisodeRow, checked: boolean) => setSelected((prev) => {
    const next = new Set(prev);
    if (checked) next.add(rowKey(row)); else next.delete(rowKey(row));
    return next;
  });
  const selectBox = (row: ApprovalEpisodeRow) => (
    <Checkbox
      checked={selected.has(rowKey(row))}
      onChange={(checked) => toggle(row, checked)}
      ariaLabel={`Select ${row.title}`}
      className="pointer-coarse:min-h-11 pointer-coarse:min-w-11 justify-center"
    />
  );

  return (
    <ReviewSection
      id="awaiting-approval"
      title="Awaiting approval"
      blurb="Processed episodes held out of the served feed until you approve them."
      query={awaiting}
      rows={rows}
      emptyText="No episodes are awaiting approval."
      errorTitle="Could not load episodes awaiting approval."
      page={page}
      onPage={(next) => { setPage(next); setSelected(new Set()); }}
      toolbar={(
        <>
          <div className="flex flex-wrap items-center gap-3 mb-3">
            <Checkbox
              checked={allChosen}
              onChange={(checked) => setSelected(checked ? new Set(rows.map(rowKey)) : new Set())}
              label="Select all"
              labelClassName="text-sm text-muted-foreground"
              className="pointer-coarse:min-h-11"
            />
            <div className="flex gap-2 w-full sm:w-auto sm:ml-auto">
              <DemoDisabled>
                <button
                  type="button"
                  disabled={approve.isPending || chosen.length === 0}
                  onClick={() => approve.mutate(chosen)}
                  className={`${btnBase} ${btnPrimary} ${touchTarget} grow basis-0 sm:grow-0 sm:basis-auto`}
                >
                  {approve.isPending ? 'Approving...' : `Approve selected (${chosen.length})`}
                </button>
              </DemoDisabled>
            </div>
          </div>
          {message && <p role="alert" className="text-sm text-destructive mb-3">{message}</p>}
        </>
      )}
      columns={[
        selectColumn(selectBox),
        episodeCell(episodePath),
        podcastCell,
        adsCell,
        heldCell,
        waitingCell,
      ]}
      renderRow={(row) => (
        <>
          {selectBox(row)}
          {rowBody(row, episodePath(row), row.pendingSince && <span className="whitespace-nowrap">Waiting {formatTimeAgo(row.pendingSince)}</span>)}
        </>
      )}
    />
  );
}

function HeldDetections() {
  const { query: held, page, setPage } = usePagedQuery('held-episodes', 'heldPage', getHeldEpisodes);
  return (
    <ReviewSection
      id="held-detections"
      title="Held detections"
      blurb="Episodes with detections that need a decision on the episode page."
      query={held}
      rows={held.data?.episodes ?? []}
      columns={[episodeCell((row) => `${episodePath(row)}#held-for-review-section`), podcastCell, adsCell, heldCell]}
      emptyText="No detections are held for review."
      errorTitle="Could not load held detections."
      page={page}
      onPage={setPage}
      renderRow={(row) => rowBody(row, `${episodePath(row)}#held-for-review-section`)}
    />
  );
}

function ReviewTab() {
  return (
    <div role="tabpanel" id="queue-panel-review" aria-labelledby="queue-tab-review" className="space-y-4">
      <AwaitingApproval />
      <HeldDetections />
    </div>
  );
}

export default ReviewTab;
