import { Link } from 'react-router';
import type { ReviewDetection } from '../../api/detections';
import { episodeOriginalUrl } from '../../api/feeds';
import type { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import { AuditionPlayButton } from '../../components/AuditionPlayButton';
import { cardActionBtn, tableActionBtn } from '../../components/rowActionStyles';
import { StageBadge } from '../../components/StageBadge';
import { SegmentCategoryBadge } from '../../components/SegmentCategoryBadge';
import {
  SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, type SegmentCategory,
} from '../../utils/segmentCategory';
import { formatTimestamp, formatDate } from '../../utils/format';
import { btnDestructive, btnOutline, btnPrimary } from '../../components/buttonStyles';
import { focusRing } from '../../components/fieldStyles';

// "Not cut" = flagged but left in the audio; the bucket covers both
// validation rejects and human "Not an ad" decisions once a recut restores
// the span (marker_status in src/detection_review.py keys on was_cut).
export const STATUS_BADGE: Record<ReviewDetection['status'], [string, string]> = {
  accepted: ['Accepted', 'bg-success/10 text-success'],
  rejected: ['Not cut', 'bg-destructive/10 text-destructive'],
  pending: ['Pending', 'bg-warning/10 text-warning'],
};

// STATUS_BADGE says what the audio did; this says what a person decided.
// Only a recorded decision gets a chip: undecided is the default here.
export const RESOLUTION_BADGE: Record<ReviewDetection['resolution'], [string, string]> = {
  unresolved: ['Not reviewed', 'bg-secondary text-muted-foreground'],
  confirmed: ['Confirmed', 'bg-success/10 text-success'],
  dismissed: ['Not an ad', 'bg-secondary text-muted-foreground'],
};

// Only beep and keep get a badge. remove is what a cut ad normally is, so
// labelling it would put a chip on nearly every row and say nothing.
const ACTION_BADGE: Record<string, [string, string]> = {
  beep: ['Beeped', 'bg-c-blue/10 text-c-blue'],
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
  if (resolution === 'unresolved') return null;
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
  // Review is bulk work, so the category is editable inline: it is what
  // decides whether a span is cut, and reaching it should not need the editor.
  onCategory?: (d: ReviewDetection, category: SegmentCategory | null) => void;
  busy: boolean;
}

function CategorySelect({ d, onCategory, busy, className = '' }: {
  d: ReviewDetection;
  onCategory: (d: ReviewDetection, category: SegmentCategory | null) => void;
  busy: boolean;
  className?: string;
}) {
  // The pick shows immediately because the mutation patches the cached list;
  // no local copy to drift out of sync.
  return (
    <select
      aria-label={`Category for ${d.episodeTitle}`}
      value={(d.category ?? '') as string}
      disabled={busy}
      onChange={(e) => onCategory(
        d, e.target.value === '' ? null : (e.target.value as SegmentCategory))}
      className={`rounded bg-secondary text-secondary-foreground border border-border disabled:opacity-50 ${focusRing} ${className}`}
    >
      <option value="">Uncategorized</option>
      {SEGMENT_CATEGORIES.map((c) => (
        <option key={c} value={c}>{SEGMENT_CATEGORY_LABELS[c]}</option>
      ))}
    </select>
  );
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
  // Card sizing is shared with the play button beside it so the two stay the
  // same height; max-w-full + overflow-hidden in that recipe keep a
  // pathologically zoomed label from forcing page scroll.
  const btn = isCard ? cardActionBtn : tableActionBtn;
  const undecided = d.resolution === 'unresolved';
  // Its category resolves to keep, so confirm/reject would record a verdict
  // the cut can never honor (the endpoint 409s them). Edit is the way
  // through: the modal's category picker changes this span's fate.
  if (d.actionApplied === 'keep') {
    const kept = (
      <>
        {d.hasOriginalAudio && (
          <AuditionPlayButton playing={playing} onClick={onTogglePlay} size={isCard ? 'card' : 'table'} />
        )}
        <p className="text-xs text-muted-foreground min-w-0">Left in by its category</p>
      </>
    );
    const adjust = (
      <>
        {actions.onCategory && (
          <CategorySelect d={d} onCategory={actions.onCategory} busy={actions.busy}
            className={isCard ? `${btn} grow min-w-0` : 'px-2 py-1 text-xs'} />
        )}
        <button
          type="button"
          onClick={() => actions.onEdit(d)}
          disabled={actions.busy}
          className={`${btn} ${isCard ? '' : 'ml-auto '}${btnOutline} disabled:opacity-50 ${focusRing}`}
        >
          Edit
        </button>
      </>
    );
    if (!isCard) {
      return <div className="flex items-center gap-2">{kept}{adjust}</div>;
    }
    return (
      <div className="flex flex-col gap-2 pt-1">
        <div className="flex items-center gap-2">{kept}</div>
        <div className="flex items-center gap-2">{adjust}</div>
      </div>
    );
  }
  // Cards use two fixed rows rather than wrap order: verdict pair on top at
  // equal width (basis-0 grow), category and Edit below. Table rows stay on one.
  const verdicts = (
    <>
      {d.hasOriginalAudio && (
        <AuditionPlayButton playing={playing} onClick={onTogglePlay} size={isCard ? 'card' : 'table'} />
      )}
      {actions.onApprove && undecided && (
        <button
          type="button"
          onClick={() => actions.onApprove?.(d)}
          disabled={actions.busy}
          className={`${btn} ${isCard ? 'grow basis-0 ' : ''}${btnPrimary} disabled:opacity-50 ${focusRing}`}
        >
          Confirm ad
        </button>
      )}
      {actions.onDismiss && undecided && (
        <button
          type="button"
          onClick={() => actions.onDismiss?.(d)}
          disabled={actions.busy}
          className={`${btn} ${isCard && actions.onApprove ? 'grow basis-0 ' : ''}${btnDestructive} disabled:opacity-50 ${focusRing}`}
        >
          Not an ad
        </button>
      )}
    </>
  );

  const secondary = (
    <>
      {actions.onCategory && (
        <CategorySelect d={d} onCategory={actions.onCategory} busy={actions.busy}
          className={isCard ? `${btn} grow min-w-0` : 'px-2 py-1 text-xs'} />
      )}
      {/* On review cards a fourth button would shrink the verdict pair, and
          the editor's Split covers them; content-sized cards have the room. */}
      {actions.onSplit && (!isCard || !actions.onApprove) && (
        <button
          type="button"
          onClick={() => actions.onSplit?.(d)}
          disabled={actions.busy}
          className={`${btn} ${btnOutline} disabled:opacity-50 ${focusRing}`}
        >
          Split
        </button>
      )}
      <button
        type="button"
        onClick={() => actions.onEdit(d)}
        disabled={actions.busy}
        className={`${btn} ${isCard ? '' : 'ml-auto '}${btnOutline} disabled:opacity-50 ${focusRing}`}
      >
        Edit
      </button>
    </>
  );

  if (!isCard) {
    return (
      <div className="flex items-center gap-1.5">{verdicts}{secondary}</div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5 min-[370px]:gap-2 pt-1">
      <div className="flex items-center gap-1.5 min-[370px]:gap-2">{verdicts}</div>
      <div className="flex items-center gap-1.5 min-[370px]:gap-2">{secondary}</div>
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
                  className={`flex-1 min-w-40 truncate text-sm font-medium text-primary hover:underline ${focusRing}`}
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
                className={`block text-sm font-medium text-primary hover:underline ${focusRing}`}
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
