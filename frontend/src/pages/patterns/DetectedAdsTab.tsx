import { useState, useEffect, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp } from 'lucide-react';
import {
  getDetections,
  type CutSummary,
  type DetectionSort,
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
import { SegmentCategoryBadge } from '../../components/SegmentCategoryBadge';
import { formatStatsDuration } from '../../utils/format';
import {
  SEGMENT_CATEGORY_FILTER_OPTIONS, UNSET_CATEGORY,
} from '../../utils/segmentCategory';
import { DetectionRows } from './DetectionRows';

const SORT_OPTIONS: Array<[DetectionSort, string]> = [
  ['date', 'Published'],
  ['confidence', 'Confidence'],
  ['podcast', 'Podcast'],
];

function StatFigure({ label, value, lead = false }: {
  label: string;
  value: string;
  lead?: boolean;
}) {
  return (
    <div>
      <p className="text-muted-foreground text-sm">{label}</p>
      <p className={lead
        ? 'font-semibold text-2xl text-foreground'
        : 'font-medium text-foreground'}
      >
        {value}
      </p>
    </div>
  );
}

// Counts beside the real badge rather than a chart: SegmentCategoryBadge renders
// every category in one tint, so a multi-hue bar would contradict the badge
// colour everywhere else in the app.
function CategoryBreakdown({ byCategory }: { byCategory: Record<string, number> }) {
  const rows = useMemo(
    () => Object.entries(byCategory)
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1]),
    [byCategory],
  );
  if (rows.length === 0) return null;
  return (
    <div className="mt-4 pt-4 border-t border-border">
      <p className="text-muted-foreground text-sm mb-2">By category</p>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {rows.map(([category, count]) => (
          <span key={category} className="flex items-center gap-1.5 text-sm">
            <SegmentCategoryBadge
              category={category === UNSET_CATEGORY ? null : category}
            />
            <span className="text-foreground font-medium">{count}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function CutStats({ summary }: { summary: CutSummary }) {
  return (
    <div className="bg-card rounded-lg border border-border p-4 mb-6">
      <h2 className="text-sm font-medium text-foreground mb-3">Ads Cut</h2>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatFigure
          label="Time cut"
          value={formatStatsDuration(summary.durationSeconds)}
          lead
        />
        <StatFigure label="Detections" value={String(summary.count)} />
        <StatFigure label="Sponsors" value={String(summary.distinctSponsors)} />
        <StatFigure label="Podcasts" value={String(summary.distinctPodcasts)} />
      </div>
      <CategoryBreakdown byCategory={summary.byCategory} />
    </div>
  );
}

export default function DetectedAdsTab() {
  const [page, setPage] = useState(1);
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
      audition.stop();
    },
    onSuccess: (_, vars) => {
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ['detections'] });
      if (vars.recut) {
        reprocessEpisode(vars.d.feedSlug, vars.d.episodeId, 'recut').catch(
          (error) => {
            console.error('Failed to trigger recut:', error);
            setActionError('Saved, but the recut did not start. It applies on the next reprocess.');
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

  // These ads were cut, so rejecting one has to put the audio back: recut from
  // the retained original.
  const dismiss = (d: ReviewDetection) => correctionMutation.mutate({
    d,
    correction: { type: 'reject', original_ad: originalAdOf(d) },
    recut: d.hasOriginalAudio,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ['detections', 'cut', page, feed, category, debouncedQ, sort, order],
    queryFn: () => getDetections({
      page,
      status: 'accepted',
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

  return (
    <div>
      {data?.cutSummary && <CutStats summary={data.cutSummary} />}

      <div className="bg-card rounded-lg border border-border p-4 mb-6 flex flex-wrap gap-4 items-center">
        <div className="flex items-center gap-2 w-full sm:w-auto min-w-0">
          <label htmlFor="detected-ads-feed" className="text-sm text-muted-foreground shrink-0">Podcast</label>
          <select
            id="detected-ads-feed"
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
          <label htmlFor="detected-ads-category" className="text-sm text-muted-foreground shrink-0">Category</label>
          <select
            id="detected-ads-category"
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
          <label htmlFor="detected-ads-q" className="text-sm text-muted-foreground shrink-0">Search</label>
          <input
            id="detected-ads-q"
            type="text"
            value={q}
            onChange={(e) => { setQ(e.target.value); }}
            placeholder="Sponsor or reason"
            className="w-full min-w-0 px-3 py-1.5 text-sm bg-secondary border border-border rounded"
          />
        </div>
        <div className="flex items-center gap-2 w-full sm:w-auto">
          <label htmlFor="detected-ads-sort" className="text-sm text-muted-foreground shrink-0">Sort</label>
          <select
            id="detected-ads-sort"
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

      {actionError && (
        <div className="text-destructive text-sm mb-3">{actionError}</div>
      )}
      {isLoading && <LoadingSpinner className="py-12" />}
      {error && (
        <div className="text-destructive text-sm">
          Failed to load detected ads.
        </div>
      )}
      {!isLoading && !error && data && (data.total === 0 ? (
        <div className="text-muted-foreground text-sm py-8 text-center">
          {feed || category || debouncedQ
            ? 'No cut ads match the current filters.'
            : 'No ads have been cut yet.'}
        </div>
      ) : (
        <>
          <DetectionRows
            detections={data.detections}
            audition={audition}
            actions={{
              onDismiss: dismiss,
              onEdit: setEditing,
              busy: correctionMutation.isPending,
            }}
            showCategory
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
            } else if (s.kind === 'reject') {
              dismiss(d);
            } else {
              closeModal();
            }
          }}
        />
      )}
    </div>
  );
}
