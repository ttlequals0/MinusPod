import { Link } from 'react-router';
import type { ReviewDetection } from '../../api/detections';
import { episodeOriginalUrl } from '../../api/feeds';
import type { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import { AuditionPlayButton } from '../../components/AuditionPlayButton';
import { StageBadge } from '../../components/StageBadge';
import { SegmentCategoryBadge } from '../../components/SegmentCategoryBadge';
import { formatTimestamp, formatDate } from '../../utils/format';
import { btnDestructive, btnOutline } from '../../components/buttonStyles';

// "Not cut" = flagged but left in the audio; the bucket covers both
// validation rejects and human "Not an ad" decisions once a recut restores
// the span (marker_status in src/detection_review.py keys on was_cut).
export const STATUS_BADGE: Record<ReviewDetection['status'], [string, string]> = {
  accepted: ['Accepted', 'bg-green-500/10 text-success'],
  rejected: ['Not cut', 'bg-red-500/10 text-red-600 dark:text-red-400'],
  pending: ['Pending', 'bg-amber-500/10 text-warning'],
};

export const RESOLUTION_BADGE: Record<ReviewDetection['resolution'], [string, string]> = {
  unresolved: ['Unresolved', 'bg-secondary text-muted-foreground'],
  confirmed: ['Confirmed', 'bg-green-500/10 text-success'],
  dismissed: ['Not an ad', 'bg-secondary text-muted-foreground'],
};

// Only beep and keep get a badge. remove is what a cut ad normally is, so
// labelling it would put a chip on nearly every row and say nothing.
const ACTION_BADGE: Record<string, [string, string]> = {
  beep: ['Beeped', 'bg-sky-500/10 text-sky-600 dark:text-sky-400'],
  keep: ['Kept', 'bg-muted text-muted-foreground'],
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

function ActionBadge({ action }: { action: string | null }) {
  const entry = action ? ACTION_BADGE[action] : undefined;
  if (!entry) return null;
  const [label, cls] = entry;
  return <span className={`px-2 py-0.5 rounded text-xs whitespace-nowrap ${cls}`}>{label}</span>;
}

function DetectionBadges({ d, showCategory }: { d: ReviewDetection; showCategory: boolean }) {
  return (
    <div className="flex gap-1.5 shrink-0">
      <DetectionStatusBadge status={d.status} />
      <ResolutionBadge resolution={d.resolution} />
      {showCategory && <ActionBadge action={d.actionApplied} />}
    </div>
  );
}

// The date/time/confidence/stage/sponsor run shared by the desktop row's
// second line and the mobile card; feedTitle placement differs per variant,
// so it stays with the callers.
function DetectionMeta({ d, showCategory }: { d: ReviewDetection; showCategory: boolean }) {
  return (
    <>
      <span>
        <span className="sr-only">published </span>
        {formatDate(d.publishDate)}
      </span>
      <span>
        <span className="sr-only">ad at </span>
        {timeLabel(d)}
      </span>
      <span>conf {d.confidence != null ? d.confidence.toFixed(2) : '-'}</span>
      {d.detectionStage ? <StageBadge stage={d.detectionStage} /> : <span>stage -</span>}
      {showCategory && <SegmentCategoryBadge category={d.category} />}
      {/* Truncation is desktop-only: the row must stay one line, while the
          card's meta line wraps and touch has no hover tooltip to recover
          text an ellipsis hides. */}
      {d.sponsor && (
        <span className="min-w-0 md:max-w-48 md:truncate" title={d.sponsor}>{d.sponsor}</span>
      )}
    </>
  );
}

export interface DetectionRowActions {
  // Each action renders only when its handler is supplied. Confirm and Not an
  // ad additionally require an unresolved row: a decided detection has nothing
  // left to decide.
  onApprove?: (d: ReviewDetection) => void;
  onDismiss?: (d: ReviewDetection) => void;
  onEdit: (d: ReviewDetection) => void;
  onSplit?: (d: ReviewDetection) => void;
  busy: boolean;
}

// One set of row actions rendered in two densities: compact at the end of
// the desktop row, touch-sized inside the mobile card footer.
function DetectionActions({ d, variant, playing, onTogglePlay, actions }: {
  d: ReviewDetection;
  variant: 'row' | 'card';
  playing: boolean;
  onTogglePlay: () => void;
  actions: DetectionRowActions;
}) {
  const isCard = variant === 'card';
  // One line everywhere: play | Confirm ad | Not an ad | Split | Edit. The
  // decision buttons grow from their content width (never below it, so labels
  // cannot wrap lopsidedly); Edit stays compact. Below 370px the labels drop to
  // text-xs with tighter padding so the row still fits a 320px screen;
  // max-w-full + overflow-hidden keep a pathologically zoomed label from
  // forcing page scroll.
  const btn = isCard
    ? 'px-2 py-2.5 text-xs min-[370px]:px-2.5 min-[370px]:text-sm rounded touch-manipulation whitespace-nowrap text-center max-w-full overflow-hidden'
    : 'px-1.5 py-1 text-xs rounded whitespace-nowrap';
  const undecided = d.resolution === 'unresolved';
  // grow exists to balance the Confirm/Not-an-ad pair on review cards. With
  // no Confirm (Detected Ads), a lone grow turns Not an ad into a full-width
  // slab, so the buttons stay content-sized there.
  const growCls = isCard && actions.onApprove ? 'grow ' : '';
  return (
    <div className={isCard ? 'flex flex-wrap items-center gap-1.5 min-[370px]:gap-2 pt-1' : 'flex items-center gap-1.5'}>
      {d.hasOriginalAudio && (
        <AuditionPlayButton playing={playing} onClick={onTogglePlay} />
      )}
      {actions.onApprove && undecided && (
        <button
          type="button"
          onClick={() => actions.onApprove?.(d)}
          disabled={actions.busy}
          className={`${btn} ${growCls}bg-green-600 hover:bg-green-700 text-white disabled:opacity-50`}
        >
          Confirm ad
        </button>
      )}
      {actions.onDismiss && undecided && (
        <button
          type="button"
          onClick={() => actions.onDismiss?.(d)}
          disabled={actions.busy}
          className={`${btn} ${growCls}${btnDestructive} disabled:opacity-50`}
        >
          Not an ad
        </button>
      )}
      {/* On review cards a fourth button would shrink the grow pair, and the
          editor's Split covers them; content-sized cards have the room. */}
      {actions.onSplit && (!isCard || !actions.onApprove) && (
        <button
          type="button"
          onClick={() => actions.onSplit?.(d)}
          disabled={actions.busy}
          className={`${btn} ${btnOutline} disabled:opacity-50`}
        >
          Split
        </button>
      )}
      <button
        type="button"
        onClick={() => actions.onEdit(d)}
        disabled={actions.busy}
        className={`${btn} ${isCard ? 'ml-auto ' : ''}${btnOutline} disabled:opacity-50`}
      >
        Edit
      </button>
    </div>
  );
}

interface DetectionRowsProps {
  detections: ReviewDetection[];
  audition: ReturnType<typeof useAuditionPlayer>;
  actions: DetectionRowActions;
  // Category and action chips are noise on the review queue, where the decision
  // is about whether the span is an ad at all, and useful on Detected Ads,
  // where the whole point is browsing by category.
  showCategory?: boolean;
}

export function DetectionRows({
  detections, audition, actions, showCategory = false,
}: DetectionRowsProps) {
  return (
    <>
      {/* Two-line rows flex to any viewport; the old fixed 9-column
          table forced horizontal scroll below its 68rem floor. */}
      <div
        className="hidden md:block bg-card rounded-lg border border-border divide-y divide-border"
        data-testid="detections-rows"
        role="list"
        aria-label="Detections"
      >
        {detections.map((d, index) => {
          const rowKey = keyOf(d, index);
          return (
            <div key={rowKey} data-testid="detection-row" role="listitem" className="px-4 py-3 hover:bg-accent/50 transition-colors">
              {/* flex-wrap + the title's min-width floor: when badges and
                  actions cannot fit beside a legible title (font scaling
                  near the md breakpoint), they wrap below instead of
                  clipping past the card edge. */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <Link
                  to={`/feeds/${d.feedSlug}/episodes/${d.episodeId}`}
                  title={d.episodeTitle}
                  className="flex-1 min-w-40 truncate text-sm font-medium text-primary hover:underline"
                >
                  {d.episodeTitle}
                </Link>
                <DetectionBadges d={d} showCategory={showCategory} />
                <div className="shrink-0">
                  <DetectionActions
                    d={d}
                    variant="row"
                    playing={audition.playingKey === rowKey}
                    onTogglePlay={() => audition.toggle(
                      rowKey, episodeOriginalUrl(d.feedSlug, d.episodeId), d.start, d.end)}
                    actions={actions}
                  />
                </div>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span className="min-w-0 max-w-56 truncate" title={d.feedTitle}>{d.feedTitle}</span>
                <DetectionMeta d={d} showCategory={showCategory} />
              </div>
            </div>
          );
        })}
      </div>
      <div className="md:hidden space-y-3" data-testid="detections-cards" role="list" aria-label="Detections">
        {detections.map((d, index) => {
          const rowKey = keyOf(d, index);
          return (
            <div key={rowKey} role="listitem" className="bg-card rounded-lg border border-border p-4 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs text-muted-foreground min-w-0 truncate">{d.feedTitle}</span>
                <DetectionBadges d={d} showCategory={showCategory} />
              </div>
              <Link
                to={`/feeds/${d.feedSlug}/episodes/${d.episodeId}`}
                className="block text-sm font-medium text-primary hover:underline"
              >
                {d.episodeTitle}
              </Link>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <DetectionMeta d={d} showCategory={showCategory} />
              </div>
              <DetectionActions
                d={d}
                variant="card"
                playing={audition.playingKey === rowKey}
                onTogglePlay={() => audition.toggle(
                  rowKey, episodeOriginalUrl(d.feedSlug, d.episodeId), d.start, d.end)}
                actions={actions}
              />
            </div>
          );
        })}
      </div>
    </>
  );
}

export default DetectionRows;
