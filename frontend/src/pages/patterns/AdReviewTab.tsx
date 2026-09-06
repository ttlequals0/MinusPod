import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getDetections,
  type DetectionReviewerFilter,
  type DetectionSort,
  type DetectionStatusFilter,
  type ReviewDetection,
} from '../../api/detections';
import { feedsQueryOptions } from '../../api/feeds';
import { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import AdReviewModal, {
  type AdReviewItem,
  type AdReviewSubmit,
} from '../../components/AdReviewModal';
import { Pagination } from '../../components/Pagination';
import LoadingSpinner from '../../components/LoadingSpinner';
import {
  DetectionRows, RESOLUTION_BADGE, STATUS_BADGE,
} from './DetectionRows';
import { DetectionFilterBar } from './DetectionFilterBar';
import { useDetectionCorrections } from './useDetectionCorrections';
import { PendingRecutsBar } from './PendingRecutsBar';
import { sortFeeds } from '../../utils/feedSort';

const STATUS_OPTIONS: Array<[DetectionStatusFilter, string]> = [
  ['needs_review', 'Needs review'],
  ['pending', 'Pending review'],
  ['rejected', 'Not cut'],
  ['accepted', 'Accepted'],
  ['all', 'All'],
];

export default function AdReviewTab() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<DetectionStatusFilter>('needs_review');
  const [feed, setFeed] = useState('');
  const [category, setCategory] = useState('');
  const [reviewer, setReviewer] = useState<DetectionReviewerFilter>('');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [sort, setSort] = useState<DetectionSort>('date');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');

  const audition = useAuditionPlayer();
  const [editing, setEditing] = useState<ReviewDetection | null>(null);
  const closeModal = () => setEditing(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(q);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [q]);

  const {
    approve, dismiss, recategorize, adjust, busy, actionError,
  } = useDetectionCorrections({
    stopAudition: audition.stop,
    onSettled: () => setEditing(null),
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ['detections', page, status, feed, category, reviewer, debouncedQ, sort, order],
    queryFn: () => getDetections({
      page,
      status,
      feed: feed || undefined,
      category: category || undefined,
      reviewer: reviewer || undefined,
      q: debouncedQ || undefined,
      sort,
      order,
    }),
  });

  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const sortedFeeds = feeds ? sortFeeds(feeds, 'title') : undefined;

  const counts = data?.counts;

  return (
    <div>
      {counts && (
        <div className="bg-card rounded-lg border border-border p-4 mb-6">
          <h2 className="text-sm font-medium text-foreground mb-3">Detection Statistics</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4 text-sm">
            <div>
              <p className="text-muted-foreground">Total</p>
              <p className="font-medium text-foreground">{counts.total}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Needs Review</p>
              <p className={`font-medium ${counts.needsReview > 0 ? 'text-warning' : 'text-foreground'}`}>
                {counts.needsReview}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Pending</p>
              <p className="font-medium text-foreground">{counts.pending}</p>
            </div>
            <div>
              <p className="text-muted-foreground">{STATUS_BADGE.rejected[0]}</p>
              <p className="font-medium text-foreground">{counts.rejected}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Accepted</p>
              <p className="font-medium text-success">{counts.accepted}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Confirmed</p>
              <p className="font-medium text-foreground">{counts.confirmed}</p>
            </div>
            <div>
              <p className="text-muted-foreground">{RESOLUTION_BADGE.dismissed[0]}</p>
              <p className="font-medium text-foreground">{counts.dismissed}</p>
            </div>
          </div>
        </div>
      )}
      <DetectionFilterBar
        idPrefix="ad-review"
        feeds={sortedFeeds}
        feed={feed}
        onFeedChange={(v) => { setFeed(v); setPage(1); }}
        category={category}
        onCategoryChange={(v) => { setCategory(v); setPage(1); }}
        reviewer={reviewer}
        onReviewerChange={(v) => { setReviewer(v); setPage(1); }}
        q={q}
        onQChange={setQ}
        sort={sort}
        onSortChange={(v) => { setSort(v); setOrder('desc'); setPage(1); }}
        order={order}
        onOrderChange={(v) => { setOrder(v); setPage(1); }}
        status={{
          value: status,
          onChange: (v) => { setStatus(v); setPage(1); },
          options: STATUS_OPTIONS,
        }}
      />

      <PendingRecutsBar />
      {actionError && (
        <div className="text-destructive text-sm mb-3">{actionError}</div>
      )}
      {isLoading && <LoadingSpinner className="py-12" />}
      {error && (
        <div className="text-destructive text-sm">
          Failed to load detections.
        </div>
      )}
      {!isLoading && !error && data && (data.total === 0 ? (
        <div className="text-muted-foreground text-sm py-8 text-center">
          {status === 'needs_review'
            ? 'No detections need review.'
            : 'No detections match the current filters.'}
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
              onCategory: recategorize,
              busy,
            }}
          />
          <Pagination page={data.page} totalPages={data.totalPages} total={data.total} onPage={setPage} />
        </>
      ))}

      {audition.audioElement}
      {editing && (
        <AdReviewModal
          item={{
            podcastSlug: editing.feedSlug,
            episodeId: editing.episodeId,
            start: editing.start,
            end: editing.end,
            sponsor: editing.sponsor,
            reason: editing.reason,
            confidence: editing.confidence,
            detectionStage: editing.detectionStage,
            patternId: editing.patternId,
            correctedBounds: null,
            category: editing.category as AdReviewItem['category'],
            actionApplied: editing.actionApplied,
          } satisfies AdReviewItem}
          episodeDuration={editing.episodeDuration ?? 0}
          hasOriginal={editing.hasOriginalAudio}
          audioMode={editing.hasOriginalAudio ? 'original' : 'processed'}
          processedAudioUrl={editing.processedUrl}
          onClose={closeModal}
          onSkip={closeModal}
          onSubmit={(s: AdReviewSubmit) => {
            const d = editing;
            if (s.kind === 'adjust') {
              adjust(d, s.adjustedStart, s.adjustedEnd, s.sponsor);
            } else if (s.kind === 'confirm') {
              approve(d);
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
