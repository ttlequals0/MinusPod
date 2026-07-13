import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  getDetections,
  type DetectionSort,
  type DetectionStatusFilter,
  type ReviewDetection,
} from '../../api/detections';
import { episodeOriginalUrl, getFeeds, reprocessEpisode } from '../../api/feeds';
import { submitCorrection, type PatternCorrection } from '../../api/patterns';
import { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import AdReviewModal, {
  type AdReviewItem,
  type AdReviewSubmit,
} from '../../components/AdReviewModal';
import { Pagination } from '../../components/Pagination';
import LoadingSpinner from '../../components/LoadingSpinner';
import { AuditionPlayButton } from '../../components/AuditionPlayButton';
import { StageBadge } from '../../components/StageBadge';
import { formatTimestamp, formatDate } from '../../utils/format';

const STATUS_OPTIONS: Array<[DetectionStatusFilter, string]> = [
  ['needs_review', 'Needs review'],
  ['pending', 'Pending review'],
  ['rejected', 'Auto-rejected'],
  ['accepted', 'Accepted'],
  ['all', 'All'],
];

const SORT_OPTIONS: Array<[DetectionSort, string]> = [
  ['date', 'Published'],
  ['confidence', 'Conf.'],
  ['podcast', 'Podcast'],
];

// "Auto-rejected" = the pipeline held the detection below the cut threshold;
// distinct from a human marking it "Not an ad".
const STATUS_BADGE: Record<ReviewDetection['status'], [string, string]> = {
  accepted: ['Accepted', 'bg-green-500/10 text-green-600 dark:text-green-400'],
  rejected: ['Auto-rejected', 'bg-red-500/10 text-red-600 dark:text-red-400'],
  pending: ['Pending', 'bg-amber-500/10 text-amber-600 dark:text-amber-400'],
};

const RESOLUTION_BADGE: Record<ReviewDetection['resolution'], [string, string]> = {
  unresolved: ['Unresolved', 'bg-secondary text-muted-foreground'],
  confirmed: ['Confirmed', 'bg-green-500/10 text-green-600 dark:text-green-400'],
  dismissed: ['Not an ad', 'bg-secondary text-muted-foreground'],
};

// Same audition key for the desktop row and its mobile card twin, so the
// playing state stays in sync across the responsive variants.
const keyOf = (d: ReviewDetection, index: number) =>
  `${d.feedSlug}-${d.episodeId}-${d.start}-${d.end}-${index}`;

const timeLabel = (d: ReviewDetection) =>
  `${formatTimestamp(d.start)} - ${formatTimestamp(d.end)} (${Math.round(d.end - d.start)}s)`;

function DetectionStatusBadge({ status }: { status: ReviewDetection['status'] }) {
  const [label, cls] = STATUS_BADGE[status];
  return <span className={`px-2 py-0.5 rounded text-xs whitespace-nowrap ${cls}`}>{label}</span>;
}

function ResolutionBadge({ resolution }: { resolution: ReviewDetection['resolution'] }) {
  const [label, cls] = RESOLUTION_BADGE[resolution];
  return <span className={`px-2 py-0.5 rounded text-xs whitespace-nowrap ${cls}`}>{label}</span>;
}

// One set of row actions rendered in two densities: compact at the end of
// the desktop row, touch-sized inside the mobile card footer.
function DetectionActions({ d, variant, playing, onTogglePlay, onApprove, onDismiss, onEdit, busy }: {
  d: ReviewDetection;
  variant: 'row' | 'card';
  playing: boolean;
  onTogglePlay: () => void;
  onApprove: () => void;
  onDismiss: () => void;
  onEdit: () => void;
  busy: boolean;
}) {
  const isCard = variant === 'card';
  const btn = isCard
    ? 'flex-1 px-3 py-2 text-sm rounded touch-manipulation'
    : 'px-1.5 py-1 text-xs rounded';
  return (
    <div className={isCard ? 'flex flex-wrap items-center gap-2 pt-1' : 'flex items-center gap-1.5'}>
      {d.hasOriginalAudio && (
        <AuditionPlayButton playing={playing} onClick={onTogglePlay} />
      )}
      {d.resolution === 'unresolved' && (
        <>
          <button
            type="button"
            onClick={onApprove}
            disabled={busy}
            className={`${btn} bg-green-600 hover:bg-green-700 text-white disabled:opacity-50`}
          >
            Confirm ad
          </button>
          <button
            type="button"
            onClick={onDismiss}
            disabled={busy}
            className={`${btn} bg-destructive hover:bg-destructive/90 text-destructive-foreground disabled:opacity-50`}
          >
            Not an ad
          </button>
        </>
      )}
      <button
        type="button"
        onClick={onEdit}
        disabled={busy}
        className={`${btn} border border-border hover:bg-accent disabled:opacity-50`}
      >
        Edit
      </button>
    </div>
  );
}

export default function AdReviewTab() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<DetectionStatusFilter>('needs_review');
  const [feed, setFeed] = useState('');
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
    queryKey: ['detections', page, status, feed, debouncedQ, sort, order],
    queryFn: () => getDetections({
      page,
      status,
      feed: feed || undefined,
      q: debouncedQ || undefined,
      sort,
      order,
    }),
  });

  const { data: feeds } = useQuery({ queryKey: ['feeds'], queryFn: getFeeds });
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
              <p className={`font-medium ${counts.needsReview > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-foreground'}`}>
                {counts.needsReview}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Pending</p>
              <p className="font-medium text-foreground">{counts.pending}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Rejected</p>
              <p className="font-medium text-foreground">{counts.rejected}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Accepted</p>
              <p className="font-medium text-green-600 dark:text-green-400">{counts.accepted}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Confirmed</p>
              <p className="font-medium text-foreground">{counts.confirmed}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Not an ad</p>
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
            aria-label={order === 'desc' ? 'Sort descending' : 'Sort ascending'}
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
          {/* Two-line rows flex to any viewport; the old fixed 9-column
              table forced horizontal scroll below its 68rem floor. */}
          <div
            className="hidden md:block bg-card rounded-lg border border-border divide-y divide-border"
            data-testid="detections-rows"
          >
            {data.detections.map((d, index) => {
              const rowKey = keyOf(d, index);
              return (
                <div key={rowKey} data-testid="detection-row" className="px-4 py-3 hover:bg-accent/50 transition-colors">
                  <div className="flex items-center gap-3">
                    <Link
                      to={`/feeds/${d.feedSlug}/episodes/${d.episodeId}`}
                      title={d.episodeTitle}
                      className="flex-1 min-w-0 truncate text-sm font-medium text-primary hover:underline"
                    >
                      {d.episodeTitle}
                    </Link>
                    <div className="flex gap-1.5 shrink-0">
                      <DetectionStatusBadge status={d.status} />
                      <ResolutionBadge resolution={d.resolution} />
                    </div>
                    <div className="shrink-0" data-testid="row-actions">
                      <DetectionActions
                        d={d}
                        variant="row"
                        playing={audition.playingKey === rowKey}
                        onTogglePlay={() => audition.toggle(
                          rowKey, episodeOriginalUrl(d.feedSlug, d.episodeId), d.start, d.end)}
                        onApprove={() => approve(d)}
                        onDismiss={() => dismiss(d)}
                        onEdit={() => setEditing(d)}
                        busy={correctionMutation.isPending}
                      />
                    </div>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span className="min-w-0 max-w-56 truncate" title={d.feedTitle}>{d.feedTitle}</span>
                    <span>{formatDate(d.publishDate)}</span>
                    <span>{timeLabel(d)}</span>
                    {d.confidence != null && <span>conf {d.confidence.toFixed(2)}</span>}
                    {d.detectionStage && <StageBadge stage={d.detectionStage} />}
                    {d.sponsor && (
                      <span className="min-w-0 max-w-48 truncate" title={d.sponsor}>{d.sponsor}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="md:hidden space-y-3" data-testid="detections-cards">
            {data.detections.map((d, index) => {
              const rowKey = keyOf(d, index);
              return (
                <div key={rowKey} className="bg-card rounded-lg border border-border p-4 space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs text-muted-foreground min-w-0 truncate">{d.feedTitle}</span>
                    <div className="flex gap-1.5 shrink-0">
                      <DetectionStatusBadge status={d.status} />
                      <ResolutionBadge resolution={d.resolution} />
                    </div>
                  </div>
                  <Link
                    to={`/feeds/${d.feedSlug}/episodes/${d.episodeId}`}
                    className="block text-sm font-medium text-primary hover:underline"
                  >
                    {d.episodeTitle}
                  </Link>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span>{formatDate(d.publishDate)}</span>
                    <span>{timeLabel(d)}</span>
                    {d.confidence != null && <span>conf {d.confidence.toFixed(2)}</span>}
                    {d.detectionStage && <StageBadge stage={d.detectionStage} />}
                  </div>
                  {d.sponsor && <div className="text-sm text-foreground">{d.sponsor}</div>}
                  <DetectionActions
                    d={d}
                    variant="card"
                    playing={audition.playingKey === rowKey}
                    onTogglePlay={() => audition.toggle(
                      rowKey, episodeOriginalUrl(d.feedSlug, d.episodeId), d.start, d.end)}
                    onApprove={() => approve(d)}
                    onDismiss={() => dismiss(d)}
                    onEdit={() => setEditing(d)}
                    busy={correctionMutation.isPending}
                  />
                </div>
              );
            })}
          </div>
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
