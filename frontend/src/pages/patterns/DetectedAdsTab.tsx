import { useState, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useUrlParams, useUrlSearch, useUrlSort } from '../../hooks/useUrlParams';
import {
  getDetections,
  type CutSummary,
  type DetectionReviewerFilter,
  type ReviewDetection,
} from '../../api/detections';
import { feedsQueryOptions } from '../../api/feeds';
import { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import AdReviewModal, { type AdReviewSubmit } from '../../components/AdReviewModal';
import StatTile from '../../components/StatTile';
import { Pagination } from '../../components/Pagination';
import { SkeletonRows } from '../../components/Skeleton';
import { SegmentCategoryBadge } from '../../components/SegmentCategoryBadge';
import { formatElapsed } from '../../utils/format';
import { sortFeeds } from '../../utils/feedSort';
import { UNSET_CATEGORY } from '../../utils/segmentCategory';
import SplitMarkerModal from '../../components/SplitMarkerModal';
import { DetectionRows } from './DetectionRows';
import { DETECTION_REVIEWERS, DETECTION_SORTS, DetectionFilterBar } from './DetectionFilterBar';
import { useDetectionCorrections } from './useDetectionCorrections';
import { PendingRecutsBar } from './PendingRecutsBar';
import { adReviewProps, splitTarget } from './detectionModalProps';

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
    <section aria-label="Ads cut" className="mb-6">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatTile label="Time cut" value={formatElapsed(summary.durationSeconds)} />
        <StatTile label="Detections" value={summary.count} />
        <StatTile label="Sponsors" value={summary.distinctSponsors} />
        <StatTile label="Podcasts" value={summary.distinctPodcasts} />
      </div>
      <CategoryBreakdown byCategory={summary.byCategory} />
    </section>
  );
}

// Page, filters, search and sort live in the URL so Back and reload return to the same place.
const DETECTED_PARAM_DEFAULTS = {
  page: '1', feed: '', category: '', reviewer: '',
};

export default function DetectedAdsTab() {
  const { read, readOneOf, readPage, write } = useUrlParams(DETECTED_PARAM_DEFAULTS, { resetKey: 'page' });
  const page = readPage('page');
  const feed = read('feed');
  const category = read('category');
  const reviewer = readOneOf<DetectionReviewerFilter>('reviewer', DETECTION_REVIEWERS, '');
  const [q, setQ, debouncedQ] = useUrlSearch();
  const { sortBy: sort, sortDirection: order, onField: setSort, onDirection: setOrder } =
    useUrlSort(DETECTION_SORTS, 'date', 'desc');

  const queryClient = useQueryClient();
  const audition = useAuditionPlayer();
  const [editing, setEditing] = useState<ReviewDetection | null>(null);
  const [splitting, setSplitting] = useState<ReviewDetection | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const closeModal = () => setEditing(null);

  const {
    dismiss, recategorize, adjust, triggerRecut, busy, actionError,
  } = useDetectionCorrections({
    stopAudition: audition.stop,
    onSettled: () => setEditing(null),
  });

  // Shared by the row-level Split modal and the split launched from inside
  // the review modal. The pieces replace a cut span, so the audio has to be
  // rebuilt from the retained original for the new boundaries to apply.
  const handleSplitSaved = (d: ReviewDetection) =>
    (result: { markerCount: number; patternIds: number[] }) => {
      setSplitting(null);
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ['detections'] });
      const noun = result.markerCount === 1 ? 'ad' : 'ads';
      setNotice(d.hasOriginalAudio
        ? `Split into ${result.markerCount} ${noun}.`
        : `Split into ${result.markerCount} ${noun}. The recut applies on the next reprocess.`);
      if (d.hasOriginalAudio) triggerRecut(d);
    };

  const { data, isLoading, error } = useQuery({
    queryKey: ['detections', 'cut', page, feed, category, reviewer, debouncedQ, sort, order],
    queryFn: () => getDetections({
      page,
      status: 'accepted',
      feed: feed || undefined,
      category: category || undefined,
      reviewer: reviewer || undefined,
      q: debouncedQ || undefined,
      sort,
      order,
    }),
  });

  const { data: feeds } = useQuery({ ...feedsQueryOptions, select: (r) => r.feeds });
  const sortedFeeds = useMemo(
    () => (feeds ? sortFeeds(feeds, 'title') : undefined),
    [feeds],
  );

  return (
    <div>
      {data?.cutSummary && <CutStats summary={data.cutSummary} />}

      <DetectionFilterBar
        idPrefix="detected-ads"
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
      />

      <PendingRecutsBar />
      {actionError && (
        <div className="text-destructive text-sm mb-3">{actionError}</div>
      )}
      {notice && (
        <div className="text-success text-sm mb-3" role="status">{notice}</div>
      )}
      {isLoading && <SkeletonRows count={6} />}
      {error && (
        <div className="text-destructive text-sm">
          Could not load detected ads.
        </div>
      )}
      {!isLoading && !error && data && (data.total === 0 ? (
        <div className="text-muted-foreground text-sm py-8 text-center">
          {feed || category || reviewer || debouncedQ
            ? 'No cut ads match these filters.'
            : 'No ads cut yet.'}
        </div>
      ) : (
        <>
          <DetectionRows
            detections={data.detections}
            showAddAudio
            audition={audition}
            actions={{
              // These ads were cut, so rejecting one has to put the audio back.
              onDismiss: (d) => { setNotice(null); dismiss(d); },
              onCategory: recategorize,
              onEdit: (d) => { setNotice(null); setEditing(d); },
              onSplit: (d) => { setNotice(null); setSplitting(d); },
              busy,
            }}
            showCategory
          />
          <Pagination page={data.page} totalPages={data.totalPages} total={data.total} onPage={(next) => write({ page: String(next) })} />
        </>
      ))}

      {audition.audioElement}
      {splitting && (
        <SplitMarkerModal
          target={splitTarget(splitting)}
          onClose={() => setSplitting(null)}
          onSplit={handleSplitSaved(splitting)}
        />
      )}
      {editing && (
        <AdReviewModal
          {...adReviewProps(editing)}
          onClose={closeModal}
          onSkip={closeModal}
          hideConfirm
          onSplitSaved={handleSplitSaved(editing)}
          onSubmit={(s: AdReviewSubmit) => {
            const d = editing;
            if (s.kind === 'adjust') {
              adjust(d, s.adjustedStart, s.adjustedEnd, s.sponsor);
            } else if (s.kind === 'reject') {
              dismiss(d);
            } else if (s.kind === 'recategorize') {
              recategorize(d, s.category ?? null);
            }
          }}
        />
      )}
    </div>
  );
}
