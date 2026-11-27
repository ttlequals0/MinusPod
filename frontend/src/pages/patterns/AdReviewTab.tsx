import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useUrlParams, useUrlSearch, useUrlSort } from '../../hooks/useUrlParams';
import {
  closePendingDetections,
  getDetections,
  type DetectionReviewerFilter,
  type DetectionStatusFilter,
  type HoldReason,
  type ReviewDetection,
} from '../../api/detections';
import { feedsQueryOptions } from '../../api/feeds';
import { getErrorMessage } from '../../api/client';
import { ConfirmModal } from '../../components/Modal';
import { DemoDisabled } from '../../components/DemoDisabled';
import { btnBase, btnSecondary, touchTarget } from '../../components/buttonStyles';
import { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import AdReviewModal, { type AdReviewSubmit } from '../../components/AdReviewModal';
import SplitMarkerModal from '../../components/SplitMarkerModal';
import StatTile from '../../components/StatTile';
import { Pagination } from '../../components/Pagination';
import { SkeletonRows } from '../../components/Skeleton';
import {
  DetectionRows, RESOLUTION_BADGE, STATUS_BADGE,
} from './DetectionRows';
import { DETECTION_REVIEWERS, DETECTION_SORTS, DetectionFilterBar } from './DetectionFilterBar';
import { useDetectionCorrections } from './useDetectionCorrections';
import { PendingRecutsBar } from './PendingRecutsBar';
import { adReviewProps, splitTarget } from './detectionModalProps';
import { sortFeeds } from '../../utils/feedSort';
import { HOLD_REASON_LABELS } from '../../utils/holdReason';

const STATUS_OPTIONS: Array<[DetectionStatusFilter, string]> = [
  ['needs_review', 'Needs review'],
  ['rejected', 'Not cut'],
  ['accepted', 'Accepted'],
  ['all', 'All'],
];

const STATUS_VALUES = STATUS_OPTIONS.map(([value]) => value);
const HOLD_VALUES: ReadonlyArray<HoldReason | ''> = ['', ...(Object.keys(HOLD_REASON_LABELS) as HoldReason[])];

// Hold reasons only exist on held detections.
const HOLD_REASON_STATUSES: DetectionStatusFilter[] = ['needs_review'];


// Page, filters, search and sort live in the URL so Back and reload return to the same place.
const REVIEW_PARAM_DEFAULTS = {
  page: '1', status: 'needs_review', feed: '', category: '', reviewer: '', hold: '',
};

export default function AdReviewTab() {
  const { read, readOneOf, readPage, write } = useUrlParams(REVIEW_PARAM_DEFAULTS, { resetKey: 'page' });
  const page = readPage('page');
  const status = readOneOf('status', STATUS_VALUES, 'needs_review');
  const feed = read('feed');
  const category = read('category');
  const reviewer = readOneOf<DetectionReviewerFilter>('reviewer', DETECTION_REVIEWERS, '');
  const holdReason = readOneOf<HoldReason | ''>('hold', HOLD_VALUES, '');
  const [q, setQ, debouncedQ] = useUrlSearch();
  const { sortBy: sort, sortDirection: order, onField: setSort, onDirection: setOrder } =
    useUrlSort(DETECTION_SORTS, 'date', 'desc');

  const audition = useAuditionPlayer();
  const [editing, setEditing] = useState<ReviewDetection | null>(null);
  const [splitting, setSplitting] = useState<ReviewDetection | null>(null);
  const queryClient = useQueryClient();
  const closeModal = () => setEditing(null);
  const [confirmingClose, setConfirmingClose] = useState(false);
  const [closeResult, setCloseResult] = useState<string | null>(null);

  const {
    approve, dismiss, recategorize, adjust, busy, actionError,
  } = useDetectionCorrections({
    stopAudition: audition.stop,
    onSettled: () => setEditing(null),
  });

  // Like every other decision here, the recut waits for the Apply bar.
  const handleSplit = () => {
    setSplitting(null);
    queryClient.invalidateQueries({ queryKey: ['detections'] });
    queryClient.invalidateQueries({ queryKey: ['pending-recuts'] });
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ['detections', page, status, feed, category, reviewer, holdReason, debouncedQ, sort, order],
    queryFn: () => getDetections({
      page,
      status,
      feed: feed || undefined,
      category: category || undefined,
      reviewer: reviewer || undefined,
      holdReason: holdReason || undefined,
      q: debouncedQ || undefined,
      sort,
      order,
    }),
  });

  // Counts ignore the feed filter, so a feed-scoped total needs its own query.
  const { data: feedPending } = useQuery({
    queryKey: ['detections', 'pending-scope', feed],
    queryFn: () => getDetections({ status: 'pending', feed, limit: 1 }),
    select: (r) => r.total,
    enabled: feed !== '',
  });

  const closePending = useMutation({
    mutationFn: () => closePendingDetections(feed || undefined),
    onSuccess: ({ closed, episodes, skipped = 0 }) => {
      setCloseResult(
        `${closed} ${closed === 1 ? 'detection' : 'detections'} closed in ${episodes} ${episodes === 1 ? 'episode' : 'episodes'}.`
        + (skipped > 0 ? ` ${skipped} ${skipped === 1 ? 'episode was' : 'episodes were'} skipped because ${skipped === 1 ? 'it' : 'they'} changed during the close.` : ''),
      );
      queryClient.invalidateQueries({ queryKey: ['detections'] });
      queryClient.invalidateQueries({ queryKey: ['episode'] });
    },
    onSettled: () => setConfirmingClose(false),
  });

  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const sortedFeeds = feeds ? sortFeeds(feeds, 'title') : undefined;

  const counts = data?.counts;
  const pendingTotal = (feed ? feedPending : counts?.pending) ?? 0;
  const holdReasonOptions = (Object.entries(HOLD_REASON_LABELS) as Array<[HoldReason, string]>)
    .map(([value, label]): [string, string] => {
      const n = counts?.pendingByHoldReason?.[value];
      return [value, n ? `${label} (${n})` : label];
    });

  return (
    <div>
      {counts && (
        <section aria-label="Detection statistics" className="grid grid-cols-3 lg:grid-cols-6 gap-2.5 sm:gap-4 mb-6">
          <StatTile label="Total" value={counts.total} />
          <StatTile label="Needs review" value={counts.needsReview} tone={counts.needsReview > 0 ? 'warning' : 'default'} />
          <StatTile label={STATUS_BADGE.rejected[0]} value={counts.rejected} />
          <StatTile label="Accepted" value={counts.accepted} tone="success" />
          <StatTile label="Confirmed" value={counts.confirmed} />
          <StatTile label={RESOLUTION_BADGE.dismissed[0]} value={counts.dismissed} />
        </section>
      )}
      <DetectionFilterBar
        idPrefix="ad-review"
        feeds={sortedFeeds}
        feed={feed}
        onFeedChange={(v) => write({ feed: v })}
        category={category}
        onCategoryChange={(v) => write({ category: v })}
        reviewer={reviewer}
        onReviewerChange={(v) => write({ reviewer: v })}
        q={q}
        onQChange={setQ}
        sort={sort}
        onSortChange={setSort}
        order={order}
        onOrderChange={setOrder}
        status={{
          value: status,
          onChange: (v) => write({
            status: v,
            ...(!HOLD_REASON_STATUSES.includes(v) && { hold: '' }),
          }),
          options: STATUS_OPTIONS,
        }}
        holdReason={HOLD_REASON_STATUSES.includes(status) ? {
          value: holdReason,
          onChange: (v) => write({ hold: v }),
          options: holdReasonOptions,
        } : undefined}
      />

      <PendingRecutsBar />
      {pendingTotal > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <DemoDisabled>
            <button
              type="button"
              onClick={() => { closePending.reset(); setCloseResult(null); setConfirmingClose(true); }}
              disabled={closePending.isPending}
              className={`${btnBase} ${btnSecondary} ${touchTarget}`}
            >
              Close all needing review
            </button>
          </DemoDisabled>
          <span className="text-sm text-muted-foreground">
            {pendingTotal} {pendingTotal === 1 ? 'detection needs' : 'detections need'} review
          </span>
        </div>
      )}
      {closeResult && (
        <div role="status" className="mb-3 text-sm text-foreground">{closeResult}</div>
      )}
      {closePending.isError && (
        <div role="alert" className="mb-3 text-destructive text-sm">
          {getErrorMessage(closePending.error, 'Could not close the detections needing review.')}
        </div>
      )}
      {actionError && (
        <div className="text-destructive text-sm mb-3">{actionError}</div>
      )}
      {isLoading && <SkeletonRows count={6} />}
      {error && (
        <div className="text-destructive text-sm">
          Could not load detections.
        </div>
      )}
      {!isLoading && !error && data && (data.total === 0 ? (
        <div className="text-muted-foreground text-sm py-8 text-center">
          {status === 'needs_review'
            ? 'No detections need review.'
            : 'No detections match these filters.'}
        </div>
      ) : (
        <>
          <DetectionRows
            detections={data.detections}
            audition={audition}
            actions={{
              onApprove: approve,
              onDismiss: dismiss,
              onEdit: setEditing,
              onSplit: setSplitting,
              onCategory: recategorize,
              busy,
            }}
          />
          <Pagination page={data.page} totalPages={data.totalPages} total={data.total} onPage={(next) => write({ page: String(next) })} />
        </>
      ))}

      {confirmingClose && (
        <ConfirmModal
          title="Close all detections needing review?"
          confirmLabel="Close all needing review"
          busyLabel="Closing..."
          destructive={false}
          pending={closePending.isPending}
          onCancel={() => setConfirmingClose(false)}
          onConfirm={() => closePending.mutate()}
        >
          <p>
            Up to {pendingTotal} {pendingTotal === 1 ? 'detection' : 'detections'} {feed ? 'in this feed' : 'across all feeds'} will
            be closed and listed as Not cut. The audio is not changed, and you can still cut each one later.
            Detections you already approved, and those on feeds set to require review, stay open.
          </p>
        </ConfirmModal>
      )}
      {audition.audioElement}
      {splitting && (
        <SplitMarkerModal
          target={splitTarget(splitting)}
          onClose={() => setSplitting(null)}
          onSplit={handleSplit}
        />
      )}
      {editing && (
        <AdReviewModal
          {...adReviewProps(editing)}
          onClose={closeModal}
          onSkip={closeModal}
          onSubmit={(s: AdReviewSubmit) => {
            const d = editing;
            if (s.kind === 'adjust') {
              adjust(d, s.adjustedStart, s.adjustedEnd, s.sponsor);
            } else if (s.kind === 'confirm') {
              approve(d, s.sponsor);
            } else if (s.kind === 'recategorize') {
              recategorize(d, s.category ?? null);
            } else {
              dismiss(d);
            }
          }}
        />
      )}
    </div>
  );
}
