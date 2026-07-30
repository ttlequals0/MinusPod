import { useState, useEffect, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getDetections,
  type CutSummary,
  type DetectionSort,
  type ReviewDetection,
} from '../../api/detections';
import { feedsQueryOptions, reprocessEpisode } from '../../api/feeds';
import { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import AdReviewModal, {
  type AdReviewItem,
  type AdReviewSubmit,
} from '../../components/AdReviewModal';
import { Pagination } from '../../components/Pagination';
import LoadingSpinner from '../../components/LoadingSpinner';
import { SegmentCategoryBadge } from '../../components/SegmentCategoryBadge';
import { formatStatsDuration } from '../../utils/format';
import { UNSET_CATEGORY } from '../../utils/segmentCategory';
import SplitMarkerModal from '../../components/SplitMarkerModal';
import { DetectionRows } from './DetectionRows';
import { DetectionFilterBar } from './DetectionFilterBar';
import { useDetectionCorrections } from './useDetectionCorrections';

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
  const [splitting, setSplitting] = useState<ReviewDetection | null>(null);
  const closeModal = () => setEditing(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQ(q);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [q]);

  const {
    dismiss, adjust, busy, actionError, setActionError,
  } = useDetectionCorrections({
    stopAudition: audition.stop,
    onSettled: () => setEditing(null),
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

      <DetectionFilterBar
        idPrefix="detected-ads"
        feeds={sortedFeeds}
        feed={feed}
        onFeedChange={(v) => { setFeed(v); setPage(1); }}
        category={category}
        onCategoryChange={(v) => { setCategory(v); setPage(1); }}
        q={q}
        onQChange={setQ}
        sort={sort}
        onSortChange={(v) => { setSort(v); setOrder('desc'); setPage(1); }}
        order={order}
        onOrderChange={(v) => { setOrder(v); setPage(1); }}
      />

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
              // These ads were cut, so rejecting one has to put the audio back.
              onDismiss: (d) => dismiss(d, d.hasOriginalAudio),
              onEdit: setEditing,
              onSplit: setSplitting,
              busy,
            }}
            showCategory
          />
          <Pagination page={data.page} totalPages={data.totalPages} total={data.total} onPage={setPage} />
        </>
      ))}

      {audition.audioElement}
      {splitting && (
        <SplitMarkerModal
          target={{
            podcastSlug: splitting.feedSlug,
            episodeId: splitting.episodeId,
            start: splitting.start,
            end: splitting.end,
          }}
          onClose={() => setSplitting(null)}
          onSplit={() => {
            const d = splitting;
            setSplitting(null);
            queryClient.invalidateQueries({ queryKey: ['detections'] });
            // The pieces replace a cut span, so the audio has to be rebuilt
            // from the retained original for the new boundaries to apply.
            if (d.hasOriginalAudio) {
              reprocessEpisode(d.feedSlug, d.episodeId, 'recut').catch((error) => {
                console.error('Failed to trigger recut:', error);
                setActionError('Split saved, but the recut did not start. It applies on the next reprocess.');
              });
            }
          }}
        />
      )}
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
              adjust(d, s.adjustedStart, s.adjustedEnd, s.sponsor);
            } else if (s.kind === 'reject') {
              dismiss(d, d.hasOriginalAudio);
            } else {
              closeModal();
            }
          }}
        />
      )}
    </div>
  );
}
