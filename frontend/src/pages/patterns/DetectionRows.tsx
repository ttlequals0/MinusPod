import type { CSSProperties, ReactNode } from 'react';
import { Check, Pencil, UnfoldHorizontal, Undo2, X, type LucideIcon } from 'lucide-react';
import { Link } from 'react-router';
import { DEMO_AUDIO_TOOLTIP, DEMO_WRITE_TOOLTIP } from '../../demo/copy';
import { IS_DEMO } from '../../demo/flag';
import type { ReviewDetection } from '../../api/detections';
import { episodeOriginalUrl } from '../../api/feeds';
import type { useAuditionPlayer } from '../../hooks/useAuditionPlayer';
import { AuditionPlayButton } from '../../components/AuditionPlayButton';
import { StageBadge } from '../../components/StageBadge';
import { SegmentCategoryBadge } from '../../components/SegmentCategoryBadge';
import {
  SEGMENT_CATEGORIES, SEGMENT_CATEGORY_LABELS, confirmLabel, isKeepLike, rejectLabel, type SegmentCategory,
} from '../../utils/segmentCategory';
import { formatTimestamp, formatDate, formatPercent } from '../../utils/format';
import { btnSuccess, btnOutline, btnDestructive } from '../../components/buttonStyles';
import { focusRing, selectBase } from '../../components/fieldStyles';
import { badgeBase, tint } from '../../components/badgeStyles';
import { AutoClosedNote } from '../../components/AutoClosedNote';
import AddAudioToPatternButton from '../../components/AddAudioToPatternButton';
import { DemoDisabled } from '../../components/DemoDisabled';
import { cardActionBtn, tableActionBtn } from '../../components/rowActionStyles';

// "Not cut" = flagged but left in the audio; the bucket covers both
// validation rejects and human "Not ad" decisions once a recut restores
// the span (marker_status in src/detection_review.py keys on was_cut).
export const STATUS_BADGE: Record<ReviewDetection['status'], [string, string]> = {
  accepted: ['Accepted', tint.success],
  rejected: ['Not cut', tint.destructive],
  pending: ['Needs review', tint.warning],
};

// STATUS_BADGE says what the audio did; this says what a person decided.
// Only a recorded decision gets a chip: undecided is the default here.
export const RESOLUTION_BADGE: Record<ReviewDetection['resolution'], [string, string]> = {
  unresolved: ['Not reviewed', tint.secondary],
  confirmed: ['Confirmed', tint.success],
  dismissed: ['Not ad', tint.secondary],
};

// Only beep, keep, and mark get a badge. remove is what a cut ad normally
// is, so labelling it would put a chip on nearly every row and say nothing.
const ACTION_BADGE: Record<string, [string, string]> = {
  beep: ['Beeped', tint.blue],
  keep: ['Kept', tint.neutral],
  mark: ['Marked', tint.neutral],
};

// Same audition key for the desktop row and its mobile card twin, so the
// playing state stays in sync across the responsive variants.
const keyOf = (d: ReviewDetection, index: number) =>
  `${d.feedSlug}-${d.episodeId}-${d.start}-${d.end}-${index}`;

const timeLabel = (d: ReviewDetection) =>
  `${formatTimestamp(d.start)} - ${formatTimestamp(d.end)} (${Math.round(d.end - d.start)}s)`;

// One chip shape behind the four badge lookups a detection row renders.
function Chip({ label, cls, title }: { label: string; cls: string; title?: string }) {
  return <span className={`${badgeBase} whitespace-nowrap ${cls}`} title={title}>{label}</span>;
}

function DetectionStatusBadge({ status }: { status: ReviewDetection['status'] }) {
  const [label, cls] = STATUS_BADGE[status];
  return <Chip label={label} cls={cls} />;
}

function ResolutionBadge({ resolution }: { resolution: ReviewDetection['resolution'] }) {
  if (resolution === 'unresolved') return null;
  const [label, cls] = RESOLUTION_BADGE[resolution];
  return <Chip label={label} cls={cls} />;
}

function ActionBadge({ action }: { action: string | null }) {
  const entry = action ? ACTION_BADGE[action] : undefined;
  if (!entry) return null;
  return <Chip label={entry[0]} cls={entry[1]} />;
}

function ReviewerBadge({ d }: { d: ReviewDetection }) {
  if (!d.reviewerMoved || d.reviewerOriginalStart == null || d.reviewerOriginalEnd == null) return null;
  const title = `Reviewer moved this from ${formatTimestamp(d.reviewerOriginalStart)} - ${formatTimestamp(d.reviewerOriginalEnd)}`;
  return <Chip label="Adjusted" cls={tint.blue} title={title} />;
}

// Everything beside the status badge: a recorded decision, a reviewer move, and the applied action.
function SecondaryBadges({ d, showCategory }: { d: ReviewDetection; showCategory: boolean }) {
  return (
    <>
      <ResolutionBadge resolution={d.resolution} />
      <ReviewerBadge d={d} />
      {showCategory && <ActionBadge action={d.actionApplied} />}
    </>
  );
}

function DetectionBadges({ d, showCategory }: { d: ReviewDetection; showCategory: boolean }) {
  return (
    <div className="flex gap-1.5 shrink-0">
      <DetectionStatusBadge status={d.status} />
      <SecondaryBadges d={d} showCategory={showCategory} />
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
      <span>{d.confidence != null ? `${formatPercent(d.confidence)} confidence` : 'confidence -'}</span>
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

interface DetectionRowActions {
  // Each action renders only when its handler is supplied. Confirm and Not ad
  // additionally require an unresolved row: a decided detection has nothing
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

function CategorySelect({ d, onCategory, busy, className = '', title }: {
  d: ReviewDetection;
  onCategory: (d: ReviewDetection, category: SegmentCategory | null) => void;
  busy: boolean;
  className?: string;
  title?: string;
}) {
  // The pick shows immediately because the mutation patches the cached list;
  // no local copy to drift out of sync.
  return (
    <select
      aria-label={`Category for ${d.episodeTitle}`}
      value={(d.category ?? '') as string}
      disabled={busy}
      title={title}
      onChange={(e) => onCategory(
        d, e.target.value === '' ? null : (e.target.value as SegmentCategory))}
      className={`${selectBase} disabled:opacity-50 ${className}`}
    >
      <option value="">Uncategorized</option>
      {SEGMENT_CATEGORIES.map((c) => (
        <option key={c} value={c}>{SEGMENT_CATEGORY_LABELS[c]}</option>
      ))}
    </select>
  );
}

// Below sm a card button is a 44px icon that grows to fill the row evenly; from sm it is the text button.
const cardBtn = `${cardActionBtn} max-sm:h-11 max-sm:w-11 max-sm:grow max-sm:px-0 leading-tight`;
const cardCategoryClass = 'min-h-[44px] text-left min-w-0';
// Cards: the select fills the space beside the buttons (9rem floor) or wraps to full width on phones.
// From sm it is a fixed 11rem so selects line up from card to card.
const cardSelectClass = 'flex-1 basis-36 truncate max-w-full sm:flex-none sm:w-44';

// Fixed columns after the title in a table row, in render order. A tab gets a track only for a control it can show, so
// each control starts at the same x in every row. Button tracks grow to the 44px target on coarse pointers (--tap).
const slotRem = (rem: number) => `max(${rem}rem,var(--tap,0px))`;
type RowKey = 'status' | 'play' | 'approve' | 'dismiss' | 'split' | 'edit' | 'category';
const ROW_COLUMNS: { key: RowKey; width: string; on: (a: DetectionRowActions) => boolean }[] = [
  { key: 'status', width: '6rem', on: () => true },
  { key: 'play', width: slotRem(1.75), on: () => true },
  { key: 'approve', width: slotRem(4.25), on: (a) => !!a.onApprove },
  { key: 'dismiss', width: slotRem(3.75), on: (a) => !!a.onDismiss },
  { key: 'split', width: slotRem(3), on: (a) => !!a.onSplit },
  { key: 'edit', width: slotRem(2.75), on: () => true },
  { key: 'category', width: '10rem', on: (a) => !!a.onCategory },
];
const rowColumns = (actions: DetectionRowActions) => ROW_COLUMNS.filter((c) => c.on(actions));
const rowTemplate = (actions: DetectionRowActions) => rowColumns(actions).map((c) => c.width).join(' ');

// Card row: w-11 buttons and gap-2, used to size the select that wraps at the button-row width.
const TAP_PX = 44;
const GAP_PX = 8;
const CARD_REJECT_WIDTH = 'sm:w-[4.25rem]';
const outlineBtn = `${btnOutline} disabled:opacity-50 ${focusRing}`;

function CardLabel({ icon: Icon, text }: { icon: LucideIcon; text: string }) {
  return (
    <>
      <Icon className="w-3.5 h-3.5 sm:hidden" aria-hidden="true" />
      <span className="max-sm:sr-only">{text}</span>
    </>
  );
}

// The row shows d.start to d.end, and that span is what the audio is built from.
function AddAudio({ d, showAddAudio, buttonClassName, className = '' }: {
  d: ReviewDetection; showAddAudio: boolean; buttonClassName: string; className?: string;
}) {
  if (!(showAddAudio && d.detectionStage === 'text_pattern' && d.patternId != null)) return null;
  return (
    <AddAudioToPatternButton
      patternId={d.patternId}
      audioState={d.patternAudioState}
      hasOriginal={d.hasOriginalAudio}
      span={{ podcastSlug: d.feedSlug, episodeId: d.episodeId, start: d.start, end: d.end }}
      buttonClassName={buttonClassName}
      className={className}
    />
  );
}

const KEPT_NOTE = 'Left in by its category';

// Row actions as grid cells in the desktop row, a wrapping touch-sized row in the mobile card.
function DetectionActions({ d, variant, keepLike, playing, onTogglePlay, actions, showAddAudio }: {
  d: ReviewDetection;
  variant: 'row' | 'card';
  keepLike: boolean;
  playing: boolean;
  onTogglePlay: () => void;
  actions: DetectionRowActions;
  showAddAudio: boolean;
}) {
  const isCard = variant === 'card';
  const btn = isCard ? cardBtn : `${tableActionBtn} w-full`;
  const label = (icon: LucideIcon, text: string) => (isCard ? <CardLabel icon={icon} text={text} /> : text);
  const cardTitle = (text: string) => (isCard ? text : undefined);
  // A keep-like category leaves no verdict to record (the endpoint 409s one); Edit's category picker is the way through.
  const undecided = d.resolution === 'unresolved' && !keepLike;
  const noAudio = IS_DEMO;
  const play = d.hasOriginalAudio && (
    <AuditionPlayButton playing={playing} onClick={onTogglePlay} size={isCard ? 'full' : 'row'}
      className={isCard ? 'max-sm:grow' : ''}
      disabled={noAudio} title={noAudio ? DEMO_AUDIO_TOOLTIP : undefined} />
  );
  const categorySelect = actions.onCategory && (
    <CategorySelect d={d} onCategory={actions.onCategory} busy={actions.busy || IS_DEMO}
      title={isCard ? (d.category ? SEGMENT_CATEGORY_LABELS[d.category as SegmentCategory] : 'Uncategorized') : undefined}
      className={isCard ? `${cardCategoryClass} ${IS_DEMO ? 'w-full' : cardSelectClass}` : 'w-full'} />
  );
  const category = IS_DEMO && categorySelect ? (
    <span className={`inline-flex cursor-not-allowed ${isCard ? `max-w-full ${cardSelectClass}` : 'w-full'}`} title={DEMO_WRITE_TOOLTIP}>{categorySelect}</span>
  ) : categorySelect;
  const edit = (
    <button
      type="button"
      onClick={() => actions.onEdit(d)}
      disabled={actions.busy}
      title={cardTitle('Edit')}
      className={`${btn} ${outlineBtn}`}
    >
      {label(Pencil, 'Edit')}
    </button>
  );
  const approve = actions.onApprove && undecided && (
    <DemoDisabled>
      <button
        type="button"
        onClick={() => actions.onApprove?.(d)}
        disabled={actions.busy}
        aria-label={confirmLabel(d.category)}
        title={cardTitle(confirmLabel(d.category))}
        className={`${btn} ${btnSuccess} disabled:opacity-50 ${focusRing}`}
      >
        {/* Short visible text; aria-label carries the category-specific name. */}
        {label(Check, 'Confirm')}
      </button>
    </DemoDisabled>
  );
  const reject = rejectLabel(d.category);
  const dismiss = actions.onDismiss && undecided && (
    <DemoDisabled>
      <button
        type="button"
        onClick={() => actions.onDismiss?.(d)}
        disabled={actions.busy}
        aria-label={reject}
        title={cardTitle(reject)}
        className={`${btn} ${isCard ? CARD_REJECT_WIDTH : ''} ${btnDestructive} disabled:opacity-50 ${focusRing}`}
      >
        {label(reject === 'Keep' ? Undo2 : X, reject)}
      </button>
    </DemoDisabled>
  );
  const split = actions.onSplit && !keepLike && (
    <DemoDisabled>
      <button
        type="button"
        onClick={() => actions.onSplit?.(d)}
        disabled={actions.busy}
        title={cardTitle('Split')}
        className={`${btn} ${outlineBtn}`}
      >
        {label(UnfoldHorizontal, 'Split')}
      </button>
    </DemoDisabled>
  );

  if (!isCard) {
    // A control hidden on one row leaves its slot empty; ROW_COLUMNS decides which slots exist.
    const cells: Partial<Record<RowKey, ReactNode>> = { play, approve, dismiss, split, edit, category };
    return (
      <>
        {rowColumns(actions).filter((c) => c.key !== 'status').map((c) => (
          <div key={c.key} className="flex min-w-0 justify-center">{cells[c.key]}</div>
        ))}
      </>
    );
  }
  const buttons = [play, approve, dismiss, split, edit].filter(Boolean);
  const btnRow = { '--btn-row': `${buttons.length * TAP_PX + (buttons.length - 1) * GAP_PX}px` } as CSSProperties;
  return (
    <div className="pt-1">
      <div data-testid="detection-actions-row" className="flex flex-wrap items-center gap-2" style={btnRow}>
        {play}{approve}{dismiss}{split}{edit}{category}
      </div>
      {keepLike && <p className="mt-2 text-xs text-muted-foreground">{KEPT_NOTE}</p>}
      {d.autoClosed && <AutoClosedNote reason={d.closedHoldReason} className="mt-2 block text-xs text-muted-foreground" />}
      <AddAudio d={d} showAddAudio={showAddAudio && !keepLike} className="mt-2"
        buttonClassName={`${cardActionBtn} ${outlineBtn} w-full`} />
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
  // Offers "Add audio to pattern" on text-pattern detections (Detected Ads only).
  showAddAudio?: boolean;
}

export function DetectionRows({
  detections, audition, actions, showCategory = false, showAddAudio = false,
}: DetectionRowsProps) {
  return (
    <>
      {/* Two-line rows flex to any viewport; the old fixed 9-column
          table forced horizontal scroll below its 68rem floor. */}
      <div
        className="hidden md:block bg-card rounded-lg border border-border divide-y divide-border [@media(pointer:coarse)]:[--tap:2.75rem]"
        data-testid="detections-rows"
        role="list"
        aria-label="Detections"
        style={{ '--cols': rowTemplate(actions) } as CSSProperties}
      >
        {detections.map((d, index) => {
          const rowKey = keyOf(d, index);
          const keepLike = isKeepLike(d.actionApplied);
          return (
            <div key={rowKey} data-testid="detection-row" role="listitem" className="px-4 py-3 hover:bg-accent/50 transition-colors">
              {/* Below lg the title takes its own line over the full width (trailing 1fr track), so the fixed columns
                  cannot squeeze it. */}
              <div className="grid grid-cols-[var(--cols)_minmax(0,1fr)] lg:grid-cols-[minmax(0,1fr)_var(--cols)] items-center gap-x-2 gap-y-1">
                <Link
                  to={`/feeds/${d.feedSlug}/episodes/${d.episodeId}`}
                  title={d.episodeTitle}
                  className={`col-span-full lg:col-span-1 min-w-0 truncate text-sm font-medium text-primary hover:underline ${focusRing}`}
                >
                  {d.episodeTitle}
                </Link>
                <div><DetectionStatusBadge status={d.status} /></div>
                <DetectionActions
                  d={d}
                  variant="row"
                  keepLike={keepLike}
                  playing={audition.playingKey === rowKey}
                  onTogglePlay={() => audition.toggle(
                    rowKey, episodeOriginalUrl(d.feedSlug, d.episodeId), d.start, d.end)}
                  actions={actions}
                  showAddAudio={showAddAudio}
                />
                <div className="col-span-full flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span className="min-w-0 max-w-56 truncate" title={d.feedTitle}>{d.feedTitle}</span>
                  <DetectionMeta d={d} showCategory={showCategory} />
                  <SecondaryBadges d={d} showCategory={showCategory} />
                  {keepLike && <span>{KEPT_NOTE}</span>}
                  {d.autoClosed && <AutoClosedNote reason={d.closedHoldReason} />}
                  <AddAudio d={d} showAddAudio={showAddAudio && !keepLike}
                    buttonClassName={`${tableActionBtn} ${outlineBtn}`} />
                </div>
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
                keepLike={isKeepLike(d.actionApplied)}
                playing={audition.playingKey === rowKey}
                onTogglePlay={() => audition.toggle(
                  rowKey, episodeOriginalUrl(d.feedSlug, d.episodeId), d.start, d.end)}
                actions={actions}
                showAddAudio={showAddAudio}
              />
            </div>
          );
        })}
      </div>
    </>
  );
}
