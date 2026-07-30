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
import { formatTime } from '../utils/adReviewHelpers';
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
  sponsors: Record<number, string>,
): PieceView[] {
  const bounds = [start, ...dividers, end];
  const out: PieceView[] = [];
  for (let i = 0; i < bounds.length - 1; i += 1) {
    // Text comes from the server's original piece geometry, which is only a
    // preview: after a drag the boundaries move but the words shown stay the
    // nearest server piece's. Good enough to identify the sponsor, and it
    // avoids a refetch on every pointer move.
    const nearest = base.find(
      (p) => p.end > bounds[i] && p.start < bounds[i + 1]);
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
  const step = Math.max(1, Math.floor(peaks.length / 400));
  const bars: number[] = [];
  for (let i = 0; i < peaks.length; i += step) {
    bars.push(peaks[i]);
  }
  const max = Math.max(...bars, 0.01);
  return (
    <div className="h-20 bg-secondary rounded flex items-center gap-px overflow-hidden" aria-hidden>
      {bars.map((v, i) => (
        <div
          key={i}
          className="flex-1 bg-primary/50"
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
  const [sponsors, setSponsors] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
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
  const { peaks, peakResolutionMs } = usePeaks(
    podcastSlug, episodeId, window_.windowStart, window_.windowEnd, 0);
  const windowPeaks = usePeakSlice(
    peaks, peakResolutionMs, window_.windowStart, window_.windowEnd);

  const pieces = useMemo(
    () => piecesFrom(start, end, effectiveDividers, data?.pieces ?? [], sponsors),
    [start, end, effectiveDividers, data?.pieces, sponsors],
  );
  const shortIdx = tooShort(pieces);

  const setDivider = (i: number, t: number) => {
    const next = [...effectiveDividers];
    next[i] = t;
    setDividers(next.sort((a, b) => a - b));
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
  };

  const removeDivider = (i: number) => {
    setDividers(effectiveDividers.filter((_, idx) => idx !== i));
    setSponsors({});
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
  const pctOf = (t: number) =>
    ((t - window_.windowStart) / windowDuration) * 100;

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

        {!isLoading && (
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
                />
              ))}
            </div>

            {/* The piece strip: one segment per resulting ad, gapped so the
                boundaries read as boundaries rather than a colour change. */}
            <div className="flex gap-0.5 h-6" data-testid="piece-strip">
              {pieces.map((p, i) => (
                <div
                  key={i}
                  className={`rounded text-[10px] flex items-center justify-center overflow-hidden ${
                    i === shortIdx
                      ? 'bg-rose-500/30 border border-rose-500'
                      : 'bg-primary/20'
                  }`}
                  style={{ width: `${Math.max(2, pctOf(p.end) - pctOf(p.start))}%` }}
                  title={`${formatTime(p.start)} to ${formatTime(p.end)}`}
                >
                  {Math.round(p.end - p.start)}s
                </div>
              ))}
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
                  <span className="text-xs font-mono text-muted-foreground w-32 shrink-0">
                    {formatTime(p.start)} to {formatTime(p.end)}
                  </span>
                  <div className="flex-1 min-w-[180px]">
                    <SponsorInput
                      value={p.sponsor}
                      onChange={(v) => setSponsors({ ...sponsors, [i]: v })}
                      sponsors={sponsorOptions}
                      placeholder="Sponsor"
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
                Ad {shortIdx + 1} is {(pieces[shortIdx].end - pieces[shortIdx].start).toFixed(1)}s.
                Ads must be at least {MIN_PIECE_SECONDS}s, so move that divider or remove it.
              </p>
            )}
            {saveError && (
              <p className="text-sm text-destructive" role="alert">{saveError}</p>
            )}

            <div className="flex items-center gap-2 pt-2">
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
