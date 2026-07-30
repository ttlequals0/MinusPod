import { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getSplitCandidates, submitSplit, type SplitPiece,
} from '../api/patterns';
import { episodeOriginalUrl } from '../api/feeds';
import { getSponsors } from '../api/sponsors';
import { useAuditionPlayer } from '../hooks/useAuditionPlayer';
import { Modal, modalPanel } from './Modal';
import LoadingSpinner from './LoadingSpinner';
import { AuditionPlayButton } from './AuditionPlayButton';
import { Pin } from './ad-editor/Pin';
import { usePeaks } from './ad-editor/usePeaks';
import { usePeakSlice } from './ad-editor/usePeakSlice';
import { useWaveformWindow } from './ad-editor/useWaveformWindow';
import ZoomControl from './ad-editor/ZoomControl';
import { SponsorInput, type SponsorOption } from './ad-editor/SponsorInput';
import {
  commitTimeInput, formatTime, timeInputKeyDown,
} from '../utils/adReviewHelpers';
import { btnGhost, btnOutline, btnPrimary } from './buttonStyles';

// Matches MIN_AD_DURATION in src/config.py. A piece shorter than this is not an
// ad the validator would accept, so the server rejects it too.
const MIN_PIECE_SECONDS = 7.0;

const ZOOM_MIN = 1;
const ZOOM_MAX = 50;

export interface SplitMarkerTarget {
  podcastSlug: string;
  episodeId: string;
  start: number;
  end: number;
}

interface Props {
  target: SplitMarkerTarget;
  onClose: () => void;
  onSplit: (result: { markerCount: number; patternIds: number[] }) => void;
}

interface PieceView {
  start: number;
  end: number;
  text: string;
  sponsor: string;
}

function piecesFrom(
  start: number, end: number, dividers: number[], base: SplitPiece[],
  sponsors: Array<string | undefined>,
): PieceView[] {
  const bounds = [start, ...dividers, end];
  const out: PieceView[] = [];
  for (let i = 0; i < bounds.length - 1; i += 1) {
    // Text is a preview from the server's original geometry, so after a drag
    // it stays the nearest piece's words. Enough to name the sponsor, and it
    // avoids a refetch per pointer move. Most-overlapping wins, or a nudged
    // divider flips the guess to whichever piece it barely touches.
    let nearest: SplitPiece | undefined;
    let bestOverlap = 0;
    for (const p of base) {
      const overlap = Math.min(p.end, bounds[i + 1]) - Math.max(p.start, bounds[i]);
      if (overlap > bestOverlap) {
        bestOverlap = overlap;
        nearest = p;
      }
    }
    out.push({
      start: bounds[i],
      end: bounds[i + 1],
      text: nearest?.text ?? '',
      sponsor: sponsors[i] ?? nearest?.sponsor ?? '',
    });
  }
  return out;
}

function tooShort(pieces: PieceView[]): number | null {
  const idx = pieces.findIndex((p) => p.end - p.start < MIN_PIECE_SECONDS);
  return idx === -1 ? null : idx;
}

// Amplitude strip for the span. Renders the peak slice directly rather than
// mounting a third WaveSurfer instance: splitting needs to see where speech
// stops, not scrub a decorative region, and the peaks are already fetched.
function PeakStrip({ peaks }: { peaks: number[] | null }) {
  if (!peaks || peaks.length === 0) {
    return <div className="h-20 bg-secondary rounded" />;
  }
  // ~200 gapless bars: per-bar gaps at this density would out-measure the
  // bars themselves on a phone-width strip and render as empty space.
  const step = Math.max(1, Math.ceil(peaks.length / 200));
  const bars: number[] = [];
  for (let i = 0; i < peaks.length; i += step) {
    let v = 0;
    for (let j = i; j < Math.min(i + step, peaks.length); j += 1) {
      v = Math.max(v, peaks[j]);
    }
    bars.push(v);
  }
  const max = Math.max(...bars, 0.01);
  return (
    <div className="h-20 bg-secondary rounded flex items-center overflow-hidden" aria-hidden>
      {bars.map((v, i) => (
        <div
          key={i}
          className="flex-1 bg-primary min-w-0"
          style={{ height: `${Math.max(2, (v / max) * 100)}%` }}
        />
      ))}
    </div>
  );
}

export default function SplitMarkerModal({ target, onClose, onSplit }: Props) {
  const { podcastSlug, episodeId, start, end } = target;
  const containerRef = useRef<HTMLDivElement>(null);
  const playheadRef = useRef((start + end) / 2);
  const audition = useAuditionPlayer();

  const [dividers, setDividers] = useState<number[] | null>(null);
  // Sparse array parallel to pieces: sponsors[i] overrides piece i's sponsor.
  const [sponsors, setSponsors] = useState<Array<string | undefined>>([]);
  // Draft text for the divider time inputs while one is being typed in;
  // null re-derives every field from the divider values (drags stay live).
  const [divInputs, setDivInputs] = useState<Record<number, string> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['splitCandidates', podcastSlug, episodeId, start, end],
    queryFn: () => getSplitCandidates(podcastSlug, episodeId, start, end),
  });
  const sponsorsQuery = useQuery({ queryKey: ['sponsors'], queryFn: () => getSponsors() });
  const sponsorOptions: SponsorOption[] = useMemo(
    () => (sponsorsQuery.data ?? []).map((s) => ({ id: s.id, name: s.name })),
    [sponsorsQuery.data],
  );

  // Server candidates seed the dividers once; after that the user owns them.
  // Memoized so the fallback array identity is stable across renders.
  const effectiveDividers = useMemo(
    () => dividers ?? (data ? data.candidates.map((c) => c.time) : []),
    [dividers, data],
  );

  const span = Math.max(0.5, end - start);
  const window_ = useWaveformWindow(
    end, (start + end) / 2, playheadRef, ZOOM_MIN, ZOOM_MAX,
    Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, end / span)),
  );
  // Fetch peaks from episode start: usePeakSlice indexes by absolute time,
  // so a window-scoped fetch would slice past the array and render nothing.
  const { peaks, peakResolutionMs } = usePeaks(
    podcastSlug, episodeId, 0, end, 0);
  const windowPeaks = usePeakSlice(
    peaks, peakResolutionMs, window_.windowStart, window_.windowEnd);

  const pieces = useMemo(
    () => piecesFrom(start, end, effectiveDividers, data?.pieces ?? [], sponsors),
    [start, end, effectiveDividers, data?.pieces, sponsors],
  );
  const shortIdx = tooShort(pieces);

  const setDivider = (i: number, t: number) => {
    const next = [...effectiveDividers];
    // Clamp within the raw neighbours instead of sorting, so divider order
    // never changes after seeding and index-keyed sponsors stay aligned.
    const lo = i === 0 ? start : next[i - 1];
    const hi = i === next.length - 1 ? end : next[i + 1];
    next[i] = Math.min(Math.max(t, lo), hi);
    setDividers(next);
    setDivInputs(null);
  };

  const addDivider = () => {
    // Drop it in the middle of the longest piece, which is where another ad is
    // most likely hiding and where there is room for one.
    let best = 0;
    pieces.forEach((p, i) => {
      if (p.end - p.start > pieces[best].end - pieces[best].start) best = i;
    });
    const mid = (pieces[best].start + pieces[best].end) / 2;
    setDividers([...effectiveDividers, mid].sort((a, b) => a - b));
    setDivInputs(null);
    // Piece `best` splits in two; its new second half has no override yet.
    setSponsors((prev) => {
      const next = [...prev];
      next.splice(best + 1, 0, undefined);
      return next;
    });
  };

  const removeDivider = (i: number) => {
    setDividers(effectiveDividers.filter((_, idx) => idx !== i));
    setDivInputs(null);
    // Pieces i and i+1 merge; the merged piece keeps the earlier override.
    setSponsors((prev) => {
      const next = [...prev];
      next.splice(i, 2, prev[i] ?? prev[i + 1]);
      return next;
    });
  };

  const submit = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const result = await submitSplit(
        podcastSlug, episodeId, { start, end }, effectiveDividers,
        pieces.map((p) => ({ sponsor: p.sponsor || undefined })),
      );
      onSplit(result);
    } catch (e) {
      console.error('Split failed', e);
      setSaveError('Failed to split. Try again.');
    } finally {
      setSaving(false);
    }
  };

  const windowDuration = Math.max(0.001, window_.windowEnd - window_.windowStart);
  // Clamped to the window so off-window pieces collapse to zero width
  // instead of getting phantom widths that misalign the strip.
  const pctOf = (t: number) => Math.min(100, Math.max(
    0, ((t - window_.windowStart) / windowDuration) * 100));

  const sponsorWrapRefs = useRef<Array<HTMLDivElement | null>>([]);
  const focusSponsor = (i: number) => {
    sponsorWrapRefs.current[i]?.querySelector('input')?.focus();
  };

  const timeInputCls = 'w-24 px-2 py-1.5 rounded-lg border border-input '
    + 'bg-background text-foreground focus:outline-hidden focus:ring-2 '
    + 'focus:ring-ring text-xs font-mono disabled:opacity-60';

  // Editable divider time field; the outer block bounds render disabled.
  const dividerField = (dividerIdx: number, label: string) => {
    const t = effectiveDividers[dividerIdx];
    return (
      <input
        type="text"
        inputMode="numeric"
        aria-label={label}
        className={timeInputCls}
        value={divInputs?.[dividerIdx] ?? formatTime(t)}
        onChange={(e) => setDivInputs({
          ...(divInputs ?? {}), [dividerIdx]: e.target.value,
        })}
        onBlur={(e) => commitTimeInput(
          e.target.value, t, end,
          (v) => setDivider(dividerIdx, v),
          () => setDivInputs(null),
        )}
        onKeyDown={timeInputKeyDown(t, () => setDivInputs(null))}
      />
    );
  };

  return (
    <Modal onClose={onClose} closeOnEscape panelClassName={`${modalPanel} max-w-4xl w-full`}>
      <div className="p-5 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Split ad block</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Drag a divider to set where one ad ends and the next begins.
          </p>
        </div>

        {isLoading && <LoadingSpinner className="py-8" />}

        {isError && (
          <div className="space-y-3">
            <p className="text-sm text-destructive" role="alert">
              Failed to load suggested dividers.
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => refetch()}
                className={`px-3 py-1.5 text-sm rounded ${btnOutline}`}
              >
                Retry
              </button>
              <button
                type="button"
                onClick={onClose}
                className={`px-3 py-1.5 text-sm rounded ${btnGhost}`}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {!isLoading && !isError && (
          <>
            {data && data.candidates.length === 0 && dividers === null && (
              <p className="text-sm text-warning">
                No sponsor transition found in this block. Add a divider where
                you hear one ad end.
              </p>
            )}

            {/* Waveform plus the dividers laid over it. */}
            <div ref={containerRef} className="relative">
              <PeakStrip peaks={windowPeaks} />
              {effectiveDividers.map((t, i) => (
                <Pin
                  key={i}
                  kind="divider"
                  boundary={t}
                  windowStart={window_.windowStart}
                  windowDuration={windowDuration}
                  containerRef={containerRef}
                  onChange={(next) => setDivider(i, next)}
                  minBoundary={i === 0 ? start : effectiveDividers[i - 1]}
                  maxBoundary={i === effectiveDividers.length - 1
                    ? end : effectiveDividers[i + 1]}
                  minSeparation={MIN_PIECE_SECONDS}
                  totalDuration={end}
                />
              ))}
            </div>

            {/* The piece strip: one segment per resulting ad, gapped so the
                boundaries read as boundaries rather than a colour change. */}
            <div className="flex gap-0.5 h-6" data-testid="piece-strip">
              {pieces.map((p, i) => {
                const width = pctOf(p.end) - pctOf(p.start);
                // Off-window pieces get no segment; their rows below remain.
                if (width <= 0) return null;
                return (
                  <button
                    type="button"
                    key={i}
                    onClick={() => focusSponsor(i)}
                    className={`rounded text-[10px] text-foreground flex items-center justify-center gap-1 px-1 overflow-hidden focus:outline-hidden focus-visible:ring-2 focus-visible:ring-ring ${
                      i === shortIdx
                        ? 'bg-rose-500/30 border border-rose-500'
                        : 'bg-primary/20'
                    }`}
                    style={{ width: `${Math.max(2, width)}%` }}
                    title={`${formatTime(p.start)} to ${formatTime(p.end)}`}
                  >
                    {p.sponsor && <span className="truncate min-w-0">{p.sponsor}</span>}
                    <span className="shrink-0">{Math.round(p.end - p.start)}s</span>
                  </button>
                );
              })}
            </div>

            <ZoomControl
              value={window_.zoom}
              min={ZOOM_MIN}
              max={ZOOM_MAX}
              onChange={(z) => window_.setZoom(z)}
              onZoomIn={window_.zoomIn}
              onZoomOut={window_.zoomOut}
            />

            {/* One row per resulting ad: play it, name its sponsor. */}
            <div className="space-y-2" data-testid="piece-rows">
              {pieces.map((p, i) => (
                <div key={i} className="flex items-center gap-2 flex-wrap">
                  <AuditionPlayButton
                    playing={audition.playingKey === `piece-${i}`}
                    onClick={() => audition.toggle(
                      `piece-${i}`, episodeOriginalUrl(podcastSlug, episodeId),
                      p.start, p.end)}
                  />
                  <span className="flex items-center gap-1 shrink-0">
                    {i === 0 ? (
                      <input type="text" disabled className={timeInputCls}
                        aria-label={`Ad ${i + 1} start time`}
                        title="The block's own start"
                        value={formatTime(p.start)} readOnly />
                    ) : dividerField(i - 1, `Ad ${i + 1} start time`)}
                    <span className="text-xs text-muted-foreground">to</span>
                    {i === pieces.length - 1 ? (
                      <input type="text" disabled className={timeInputCls}
                        aria-label={`Ad ${i + 1} end time`}
                        title="The block's own end"
                        value={formatTime(p.end)} readOnly />
                    ) : dividerField(i, `Ad ${i + 1} end time`)}
                  </span>
                  <div
                    className="flex-1 min-w-[180px] sm:max-w-72"
                    ref={(el) => { sponsorWrapRefs.current[i] = el; }}
                  >
                    <SponsorInput
                      value={p.sponsor}
                      onChange={(v) => setSponsors((prev) => {
                        const next = [...prev];
                        next[i] = v;
                        return next;
                      })}
                      sponsors={sponsorOptions}
                      placeholder="Choose or type a sponsor"
                    />
                  </div>
                  {i > 0 && (
                    <button
                      type="button"
                      onClick={() => removeDivider(i - 1)}
                      className={`px-2 py-1 text-xs rounded ${btnOutline}`}
                    >
                      Remove divider
                    </button>
                  )}
                </div>
              ))}
            </div>

            {shortIdx !== null && (
              <p className="text-sm text-destructive" role="alert">
                Ad {shortIdx + 1} is {(pieces[shortIdx].end - pieces[shortIdx].start).toFixed(1)}s,{' '}
                {(MIN_PIECE_SECONDS - (pieces[shortIdx].end - pieces[shortIdx].start)).toFixed(1)}s
                short of the {MIN_PIECE_SECONDS}s minimum. Move that divider or remove it.
              </p>
            )}
            {saveError && (
              <p className="text-sm text-destructive" role="alert">{saveError}</p>
            )}

            <div className="flex items-center gap-2 pt-2 flex-wrap">
              <button
                type="button"
                onClick={addDivider}
                className={`px-3 py-1.5 text-sm rounded ${btnOutline}`}
              >
                Add divider
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={saving || shortIdx !== null || effectiveDividers.length === 0}
                className={`ml-auto px-3 py-1.5 text-sm rounded ${btnPrimary} disabled:opacity-50`}
              >
                Split into {pieces.length} {pieces.length === 1 ? 'ad' : 'ads'}
              </button>
              <button
                type="button"
                onClick={onClose}
                className={`px-3 py-1.5 text-sm rounded ${btnGhost}`}
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
      {audition.audioElement}
    </Modal>
  );
}
