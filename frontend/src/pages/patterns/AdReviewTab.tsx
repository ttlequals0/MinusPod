import { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  getDetections,
  type DetectionSort,
  type DetectionStatusFilter,
  type ReviewDetection,
} from '../../api/detections';
import { feedsQueryOptions, reprocessEpisode } from '../../api/feeds';
import { submitCorrection, type PatternCorrection } from '../../api/patterns';
import { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import AdReviewModal, {
  type AdReviewItem,
  type AdReviewSubmit,
} from '../../components/AdReviewModal';
import { Pagination } from '../../components/Pagination';
import LoadingSpinner from '../../components/LoadingSpinner';
import { SEGMENT_CATEGORY_FILTER_OPTIONS } from '../../utils/segmentCategory';
import {
  DetectionRows, RESOLUTION_BADGE, STATUS_BADGE,
} from './DetectionRows';

const STATUS_OPTIONS: Array<[DetectionStatusFilter, string]> = [
  ['needs_review', 'Needs review'],
  ['pending', 'Pending review'],
  ['rejected', 'Not cut'],
  ['accepted', 'Accepted'],
  ['all', 'All'],
];

const SORT_OPTIONS: Array<[DetectionSort, string]> = [
  ['date', 'Published'],
  ['confidence', 'Confidence'],
  ['podcast', 'Podcast'],
];

export default function AdReviewTab() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<DetectionStatusFilter>('needs_review');
  const [feed, setFeed] = useState('');
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [sort, setSort] = useState<DetectionSort>('date');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');

  const queryClient = useQueryClient();
  const audition = useAuditionPlayer();
  const [editing, setEditing] = useState<ReviewDetection | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const closeModal = () => setEditing(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(q);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [q]);

  const correctionMutation = useMutation({
    mutationFn: async (args: {
      d: ReviewDetection;
      correction: PatternCorrection;
      recut: boolean;
    }) => {
      await submitCorrection(args.d.feedSlug, args.d.episodeId, args.correction);
    },
    onMutate: () => {
      setActionError(null);
      // A saved correction can drop the playing row on refetch; stop the
      // windowed preview up front (same guard EpisodeDetail uses).
      audition.stop();
    },
    onSuccess: (_, vars) => {
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ['detections'] });
      if (vars.recut) {
        reprocessEpisode(vars.d.feedSlug, vars.d.episodeId, 'recut').catch(
          (error) => {
            console.error('Failed to trigger recut:', error);
            setActionError('Confirmed, but the recut did not start. The cut applies on the next reprocess.');
          },
        );
      }
    },
    onError: (error) => {
      console.error('Failed to save correction:', error);
      setActionError('Failed to save correction. Try again.');
    },
  });

  const originalAdOf = (d: ReviewDetection) => ({
    start: d.start,
    end: d.end,
    pattern_id: d.patternId ?? undefined,
    confidence: d.confidence ?? undefined,
    reason: d.reason ?? undefined,
    sponsor: d.sponsor ?? undefined,
  });

  const approve = (d: ReviewDetection) => correctionMutation.mutate({
    d,
    correction: { type: 'confirm', original_ad: originalAdOf(d) },
    recut: d.hasOriginalAudio,
  });

  const dismiss = (d: ReviewDetection) => correctionMutation.mutate({
    d,
    correction: { type: 'reject', original_ad: originalAdOf(d) },
    recut: false,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ['detections', page, status, feed, category, debouncedQ, sort, order],
    queryFn: () => getDetections({
      page,
      status,
      feed: feed || undefined,
      category: category || undefined,
      q: debouncedQ || undefined,
      sort,
      order,
    }),
  });

  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const sortedFeeds = feeds
    ? [...feeds].sort((a, b) => a.title.localeCompare(b.title))
    : undefined;

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
      <div className="bg-card rounded-lg border border-border p-4 mb-6 flex flex-wrap gap-4 items-center">
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <label htmlFor="ad-review-status" className="text-sm text-muted-foreground shrink-0">Status</label>
          <select
            id="ad-review-status"
            value={status}
            onChange={(e) => { setStatus(e.target.value as DetectionStatusFilter); setPage(1); }}
            className="flex-1 sm:flex-none min-w-0 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            {STATUS_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto min-w-0">
          <label htmlFor="ad-review-feed" className="text-sm text-muted-foreground shrink-0">Podcast</label>
          <select
            id="ad-review-feed"
            value={feed}
            onChange={(e) => { setFeed(e.target.value); setPage(1); }}
            className="flex-1 sm:flex-none min-w-0 max-w-full sm:max-w-72 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            <option value="">All podcasts</option>
            {sortedFeeds?.map((f) => (
              <option key={f.slug} value={f.slug}>{f.title}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <label htmlFor="ad-review-category" className="text-sm text-muted-foreground shrink-0">Category</label>
          <select
            id="ad-review-category"
            value={category}
            onChange={(e) => { setCategory(e.target.value); setPage(1); }}
            className="flex-1 sm:flex-none min-w-0 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            {SEGMENT_CATEGORY_FILTER_OPTIONS.map(([value, label]) => (
              <option key={value || 'all'} value={value}>{label}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2 w-full sm:flex-1 sm:min-w-[200px]">
          <label htmlFor="ad-review-q" className="text-sm text-muted-foreground shrink-0">Search</label>
          <input
            id="ad-review-q"
            type="text"
            value={q}
            onChange={(e) => { setQ(e.target.value); }}
            placeholder="Sponsor or reason"
            className="w-full min-w-0 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          />
        </div>
        {/* Neither the rows nor the cards have sortable headers, so sorting
            lives in the filter bar at every width. */}
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <label htmlFor="ad-review-sort" className="text-sm text-muted-foreground shrink-0">Sort</label>
          <select
            id="ad-review-sort"
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as DetectionSort);
              setOrder('desc');
              setPage(1);
            }}
            className="flex-1 min-w-0 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          >
            {SORT_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => { setOrder(order === 'desc' ? 'asc' : 'desc'); setPage(1); }}
            aria-label={order === 'desc' ? 'Switch to ascending order' : 'Switch to descending order'}
            className="px-3 py-1.5 bg-secondary border border-border rounded text-muted-foreground"
          >
            {order === 'desc'
              ? <ChevronDown className="w-4 h-4" aria-hidden />
              : <ChevronUp className="w-4 h-4" aria-hidden />}
          </button>
        </div>
      </div>

      {/* Outside the has-rows branch so a recut/correction failure stays
          visible even when the refetch empties the current page. */}
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
              busy: correctionMutation.isPending,
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
          } satisfies AdReviewItem}
          hasOriginal={editing.hasOriginalAudio}
          audioMode={editing.hasOriginalAudio ? 'original' : 'processed'}
          processedAudioUrl={editing.processedUrl}
          onClose={closeModal}
          onSkip={closeModal}
          onSubmit={(s: AdReviewSubmit) => {
            const d = editing;
            if (s.kind === 'adjust') {
              correctionMutation.mutate({
                d,
                correction: {
                  type: 'adjust',
                  original_ad: originalAdOf(d),
                  adjusted_start: s.adjustedStart,
                  adjusted_end: s.adjustedEnd,
                  sponsor: s.sponsor,
                },
                recut: false,
              });
            } else if (s.kind === 'confirm') {
              approve(d);
            } else {
              dismiss(d);
            }
          }}
        />
      )}
    </div>
  );
}
