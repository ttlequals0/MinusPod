import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Play, Pause, SkipBack, SkipForward, Search, ChevronUp, ChevronDown } from 'lucide-react';
import {
  getOriginalSegments,
  type OriginalSegment,
  type TranscriptWord,
} from '../../api/feeds';
import { formatTime } from '../../utils/adReviewHelpers';
import { btnGhost, touchTarget } from '../buttonStyles';
import { badgeBase } from '../badgeStyles';
import { focusRing } from '../../components/fieldStyles';
import SpeedMenu from './SpeedMenu';

export interface TextRun {
  start: number;
  end: number;
  text: string;
}

interface Props {
  slug: string;
  episodeId: string;
  episodeDuration?: number;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  // Resolved selection bounds. Owned by the parent so the audio-mode
  // waveform stays in sync when the user toggles back.
  adStart: number;
  adEnd: number;
  onSelectionChange: (start: number, end: number, text: string) => void;
  // Frozen runs plus the current (unfrozen) one, in whatever order they were
  // committed. The host sorts and counts; this panel only tracks the list.
  onRunsChange: (runs: TextRun[]) => void;
  // Playback rate is owned by the parent (same audio element drives both modes).
  playbackRate: number;
  setPlaybackRate: (r: number) => void;
  disabled?: boolean;
  // Keys (via runKey) of runs the host has already saved. Fixed in the merge
  // step so a retry can't re-cover a saved range.
  savedKeys?: Set<string>;
}

const EMPTY_SAVED_KEYS = new Set<string>();

interface FlatWord extends TranscriptWord {
  globalIndex: number; // position across all segments, for selection math
}

// Runs this close in time collapse into one on freeze.
const MERGE_GAP_SECONDS = 1;
const chipClass = `${badgeBase} inline-flex items-center gap-1.5 border border-border bg-background text-foreground`;

// Shared with AdReviewModal so saved-run lookups use an identical key.
export function runKey(run: TextRun): string {
  return JSON.stringify([run.start, run.end, run.text]);
}

function textForRange(words: FlatWord[], start: number, end: number): string {
  return words
    .filter((w) => w.start >= start - 0.001 && w.end <= end + 0.001)
    .map((w) => w.word.trim())
    .filter(Boolean)
    .join(' ');
}

// Re-derives merged text from the transcript rather than concatenating the
// source runs, so an overlap doesn't duplicate words. A saved run is treated
// as fixed: it never absorbs a neighbor and never gets absorbed, so a retry
// after a partial failure can't silently re-cover an already-saved range.
function mergeRuns(runs: TextRun[], words: FlatWord[], savedKeys: Set<string>): TextRun[] {
  const sorted = [...runs].sort((a, b) => a.start - b.start);
  const merged: TextRun[] = [];
  for (const run of sorted) {
    const last = merged[merged.length - 1];
    const canMerge =
      last &&
      !savedKeys.has(runKey(last)) &&
      !savedKeys.has(runKey(run)) &&
      run.start <= last.end + MERGE_GAP_SECONDS;
    if (canMerge) {
      last.end = Math.max(last.end, run.end);
      last.text = textForRange(words, last.start, last.end);
    } else {
      merged.push({ ...run });
    }
  }
  return merged;
}

function flatten(segments: OriginalSegment[]): FlatWord[] {
  const out: FlatWord[] = [];
  let idx = 0;
  for (const seg of segments) {
    if (!seg.words || seg.words.length === 0) continue;
    for (const w of seg.words) {
      out.push({ ...w, globalIndex: idx });
      idx += 1;
    }
  }
  return out;
}

function TextSelectionPanel({
  slug,
  episodeId,
  audioRef,
  adStart,
  adEnd,
  onSelectionChange,
  onRunsChange,
  playbackRate,
  setPlaybackRate,
  disabled = false,
  savedKeys = EMPTY_SAVED_KEYS,
}: Props) {
  const [frozenRuns, setFrozenRuns] = useState<TextRun[]>([]);
  const [currentText, setCurrentText] = useState('');
  // The mouseup listener reads frozenRuns/onRunsChange through refs to avoid
  // stale closures; frozenRunsRef is set by every setter below, not a
  // useEffect, which would lag a mouseup's own setTimeout(0).
  const frozenRunsRef = useRef(frozenRuns);
  const setFrozenRunsSynced = (next: TextRun[]) => {
    frozenRunsRef.current = next;
    setFrozenRuns(next);
  };
  const onRunsChangeRef = useRef(onRunsChange);
  useEffect(() => {
    onRunsChangeRef.current = onRunsChange;
  }, [onRunsChange]);
  // Fetch state collapsed into a single object so each useEffect outcome is
  // one setState call, not a setLoadError(null) at the top of the effect plus
  // setSegments later (the latter shape triggers react-hooks/set-state-in-effect).
  const [fetchState, setFetchState] = useState<{
    segments: OriginalSegment[] | null;
    error: string | null;
  }>({ segments: null, error: null });
  const segments = fetchState.segments;
  const loadError = fetchState.error;
  const [searchTerm, setSearchTerm] = useState('');
  // React-recommended "adjust state when a prop changes without an effect":
  // store the term currentMatch was last reset for, and reconcile during render.
  const [matchSearchKey, setMatchSearchKey] = useState('');
  const [currentMatch, setCurrentMatch] = useState(0);
  if (matchSearchKey !== searchTerm) {
    setMatchSearchKey(searchTerm);
    setCurrentMatch(0);
  }
  const [isPlaying, setIsPlaying] = useState(false);

  const transcriptRef = useRef<HTMLDivElement>(null);
  // Set synchronously alongside the fetch, not read from the flatWords
  // closure: a mouseup landing before the listener effect rebinds would
  // otherwise see an empty list and silently no-op.
  const flatWordsRef = useRef<FlatWord[]>([]);
  const disabledRef = useRef(disabled);
  useLayoutEffect(() => {
    disabledRef.current = disabled;
  }, [disabled]);

  // Fetch once. The episode's words live in episode_details.original_segments_json
  // and never change after transcription, so no refetch on selection edits.
  useEffect(() => {
    let cancelled = false;
    getOriginalSegments(slug, episodeId)
      .then((res) => {
        if (cancelled) return;
        const hasWords = res.segments.some((s) => s.words && s.words.length > 0);
        flatWordsRef.current = hasWords ? flatten(res.segments) : [];
        setFetchState({
          segments: res.segments,
          error: hasWords
            ? null
            : 'This episode has no word-level timestamps. Re-transcribe to use text mode.',
        });
      })
      .catch((err) => {
        if (cancelled) return;
        setFetchState({
          segments: null,
          error: err?.message || 'Failed to load transcript',
        });
      });
    return () => {
      cancelled = true;
    };
  }, [slug, episodeId]);

  const flatWords = useMemo(() => (segments ? flatten(segments) : []), [segments]);

  // Lowercased copy of each word, computed once per fetch so each search
  // keystroke only does .includes against a precomputed string instead of
  // re-lowercasing the whole transcript.
  const lowerWords = useMemo(() => flatWords.map((w) => w.word.toLowerCase()), [flatWords]);

  const matchIndices = useMemo(() => {
    const q = searchTerm.trim().toLowerCase();
    if (!q || flatWords.length === 0) return [] as number[];
    const out: number[] = [];
    for (let i = 0; i < flatWords.length; i++) {
      if (lowerWords[i].includes(q)) out.push(flatWords[i].globalIndex);
    }
    return out;
  }, [searchTerm, flatWords, lowerWords]);

  // O(1) membership for the per-word render highlight.
  const matchSet = useMemo(() => new Set(matchIndices), [matchIndices]);

  // Derive the highlighted word range from the resolved bounds. This survives
  // focus changes (clicking into the Text template textarea below clears the
  // browser's native Selection, which would otherwise wipe the visible
  // highlight). Recomputed whenever adStart/adEnd change so audio-mode pin
  // drags also re-highlight when the user toggles back.
  const selectedRange = useMemo(() => {
    if (flatWords.length === 0 || !(adEnd > adStart)) return null;
    let startIdx = -1;
    let endIdx = -1;
    for (let i = 0; i < flatWords.length; i++) {
      const w = flatWords[i];
      if (startIdx === -1 && w.start >= adStart - 0.001) startIdx = i;
      if (w.end <= adEnd + 0.001) endIdx = i;
    }
    if (startIdx === -1 || endIdx < startIdx) return null;
    return { startIdx, endIdx };
  }, [adStart, adEnd, flatWords]);

  useEffect(() => {
    if (matchIndices.length === 0) return;
    const idx = matchIndices[currentMatch];
    const el = transcriptRef.current?.querySelector(
      `[data-widx="${idx}"]`,
    ) as HTMLElement | null;
    if (el) {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [matchIndices, currentMatch]);

  // Snap each Range endpoint to the nearest [data-widx] ancestor. O(1) per
  // endpoint via Element.closest; the previous O(words) querySelectorAll +
  // intersectsNode loop made every selection commit linear in transcript length.
  const resolveSelection = (): { startIdx: number; endIdx: number } | null => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
    const root = transcriptRef.current;
    if (!root) return null;
    const range = sel.getRangeAt(0);
    if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) {
      return null;
    }
    const startNode =
      range.startContainer.nodeType === Node.ELEMENT_NODE
        ? (range.startContainer as Element)
        : range.startContainer.parentElement;
    const endNode =
      range.endContainer.nodeType === Node.ELEMENT_NODE
        ? (range.endContainer as Element)
        : range.endContainer.parentElement;
    const startEl = startNode?.closest<HTMLElement>('[data-widx]');
    const endEl = endNode?.closest<HTMLElement>('[data-widx]');
    if (!startEl || !endEl) return null;
    const a = parseInt(startEl.dataset.widx || '-1', 10);
    const b = parseInt(endEl.dataset.widx || '-1', 10);
    if (a < 0 || b < 0) return null;
    return { startIdx: Math.min(a, b), endIdx: Math.max(a, b) };
  };

  // Pure: resolves the browser's live Selection into {start, end, text}
  // without touching React state, so freezeCurrentRun can call it directly
  // for the freshest value instead of trusting adStart/adEnd/currentText,
  // which the deferred mouseup commit below may not have applied yet.
  const resolveCurrentSelection = (): TextRun | null => {
    const resolved = resolveSelection();
    if (!resolved) return null;
    const words = flatWordsRef.current;
    const first = words[resolved.startIdx];
    const last = words[resolved.endIdx];
    if (!first || !last) return null;
    const text = words
      .slice(resolved.startIdx, resolved.endIdx + 1)
      .map((w) => w.word.trim())
      .filter(Boolean)
      .join(' ');
    return { start: first.start, end: last.end, text };
  };

  const commitSelection = () => {
    if (disabledRef.current) return;
    const current = resolveCurrentSelection();
    if (!current) return;
    setCurrentText(current.text);
    onSelectionChange(current.start, current.end, current.text);
    onRunsChangeRef.current([...frozenRunsRef.current, current]);
  };

  // Commit on mouseup/touchend, scoped to the transcript root so unrelated
  // mouseups elsewhere don't fire it. useLayoutEffect, not useEffect: a
  // passive effect would leave a mouseup with no listener right as segments load.
  useLayoutEffect(() => {
    const root = transcriptRef.current;
    if (!root) return;
    const handler = () => {
      // Defer one tick so the browser's selection state settles.
      setTimeout(commitSelection, 0);
    };
    root.addEventListener('mouseup', handler);
    root.addEventListener('touchend', handler);
    return () => {
      root.removeEventListener('mouseup', handler);
      root.removeEventListener('touchend', handler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flatWords]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onTime = () => {
      if (audio.currentTime >= adEnd) {
        audio.pause();
        audio.currentTime = adStart;
        setIsPlaying(false);
      }
    };
    audio.addEventListener('timeupdate', onTime);
    return () => audio.removeEventListener('timeupdate', onTime);
  }, [audioRef, adStart, adEnd]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || audio.paused) return;
    audio.pause();
    setIsPlaying(false);
  }, [adStart, adEnd, audioRef]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio || !(adEnd > adStart)) return;
    if (audio.paused) {
      audio.currentTime = adStart;
      audio.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
    } else {
      audio.pause();
      setIsPlaying(false);
    }
  };

  const snapTo = (t: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = t;
  };

  const selectionDuration = Math.max(0, adEnd - adStart);
  const hasSelection = selectionDuration > 0.001;

  // Freezes the current (unfrozen) run into the list and clears the
  // selection so the next drag starts a fresh one. Runs within
  // MERGE_GAP_SECONDS of each other collapse into one at this point.
  const freezeCurrentRun = () => {
    // Re-resolve the live selection first: a mouseup's commit is deferred
    // one tick (setTimeout 0), so a freeze click fired before that tick
    // runs would otherwise read adStart/adEnd/currentText before they
    // caught up, freezing a stale (or still-default, empty-text) selection.
    // Falls back to the already-committed state once the selection itself
    // is gone (e.g. a second freeze click with nothing newly selected).
    const current = resolveCurrentSelection()
      ?? (hasSelection ? { start: adStart, end: adEnd, text: currentText } : null);
    if (!current) return;
    const merged = mergeRuns(
      [...frozenRunsRef.current, current],
      flatWords,
      savedKeys,
    );
    setFrozenRunsSynced(merged);
    if (merged.length === 1) {
      // Nothing else to merge with: zeroing the selection here would
      // disable Save on this one valid span. Mirror it as the current
      // selection instead, so it stays directly saveable.
      const [only] = merged;
      setCurrentText(only.text);
      onSelectionChange(only.start, only.end, only.text);
    } else {
      setCurrentText('');
      onSelectionChange(0, 0, '');
    }
    onRunsChange(merged);
  };

  const removeRun = (index: number) => {
    const next = frozenRunsRef.current.filter((_, i) => i !== index);
    // One run left with no active selection would zero the single-run form
    // and disable Save: promote it back to the current selection instead.
    if (next.length === 1 && !hasSelection) {
      const [restored] = next;
      setFrozenRunsSynced([]);
      setCurrentText(restored.text);
      onSelectionChange(restored.start, restored.end, restored.text);
      onRunsChange([restored]);
      return;
    }
    setFrozenRunsSynced(next);
    const current = hasSelection ? [{ start: adStart, end: adEnd, text: currentText }] : [];
    onRunsChange([...next, ...current]);
  };

  if (loadError) {
    return (
      <div className="px-6 py-4">
        <p className="text-sm text-destructive">{loadError}</p>
      </div>
    );
  }

  if (!segments) {
    return (
      <div className="px-6 py-4">
        <p className="text-sm text-muted-foreground">Loading transcript...</p>
      </div>
    );
  }

  const currentMatchGlobal =
    matchIndices.length > 0 ? matchIndices[currentMatch] : -1;

  return (
    <div className="px-4 sm:px-6 py-3 space-y-3">
      {/* Search bar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search the transcript"
            disabled={disabled}
            className="w-full pl-8 pr-3 py-1.5 rounded-md border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring max-sm:min-h-11"
          />
        </div>
        {matchIndices.length > 0 && (
          <>
            <span className="text-xs text-muted-foreground tabular-nums">
              {currentMatch + 1} of {matchIndices.length}
            </span>
            <button
              type="button"
              disabled={disabled}
              onClick={() =>
                setCurrentMatch((m) => (m - 1 + matchIndices.length) % matchIndices.length)
              }
              className={`p-1 rounded ${btnGhost} ${focusRing} ${touchTarget}`}
              aria-label="Previous match"
            >
              <ChevronUp className="w-4 h-4" />
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => setCurrentMatch((m) => (m + 1) % matchIndices.length)}
              className={`p-1 rounded ${btnGhost} ${focusRing} ${touchTarget}`}
              aria-label="Next match"
            >
              <ChevronDown className="w-4 h-4" />
            </button>
          </>
        )}
        {searchTerm && matchIndices.length === 0 && (
          <span className="text-xs text-muted-foreground">No matches</span>
        )}
      </div>

      {/* Selection readout */}
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">
          Original transcript - highlight the ad copy
        </span>
        <span className="text-foreground tabular-nums">
          {hasSelection
            ? `Selection: ${formatTime(adStart)} - ${formatTime(adEnd)} (${selectionDuration.toFixed(1)}s)`
            : 'No selection yet'}
        </span>
      </div>

      {/* Spans: each frozen run is a chip with its time range and a remove
          control; "Add another span" freezes the current selection and
          starts a new one. */}
      <div className="flex items-center gap-2 flex-wrap">
        {frozenRuns.map((run, i) => (
          <span key={`${run.start.toFixed(3)}-${run.end.toFixed(3)}`} className={chipClass}>
            {formatTime(run.start)} - {formatTime(run.end)}
            <button
              type="button"
              onClick={() => removeRun(i)}
              disabled={disabled}
              aria-label={`Remove span ${formatTime(run.start)} to ${formatTime(run.end)}`}
              className={`${touchTarget} max-sm:-mx-2.5 max-sm:-my-2.5 rounded text-muted-foreground hover:text-destructive disabled:opacity-40 ${focusRing}`}
            >
              &times;
            </button>
          </span>
        ))}
        <button
          type="button"
          onClick={freezeCurrentRun}
          disabled={!hasSelection || disabled}
          className={`px-2 py-1 rounded text-xs ${btnGhost} disabled:opacity-40 disabled:cursor-not-allowed ${focusRing} max-sm:min-h-11`}
        >
          Add another span
        </button>
      </div>

      {/* Transcript. selection:bg-primary/40 keeps the active drag visible
          on mobile (default selection color is near-invisible in dark mode);
          per-word bg-primary/30 highlight below persists after the browser
          Selection clears (e.g., when focus moves to the textarea). */}
      <div
        ref={transcriptRef}
        className={`bg-secondary/40 rounded-lg p-3 max-h-[40vh] overflow-y-auto text-sm leading-relaxed select-text selection:bg-primary/50 selection:text-primary-foreground ${disabled ? 'pointer-events-none select-none' : ''}`}
      >
        {flatWords.length === 0 ? (
          <p className="text-muted-foreground">Transcript is empty.</p>
        ) : (
          flatWords.map((w) => {
            const isMatch = matchSet.has(w.globalIndex);
            const isCurrent = w.globalIndex === currentMatchGlobal;
            const isSelected =
              selectedRange !== null &&
              w.globalIndex >= selectedRange.startIdx &&
              w.globalIndex <= selectedRange.endIdx;
            const className = isSelected
              ? 'bg-primary/30 text-foreground rounded-sm'
              : isCurrent
                ? 'bg-primary/40 text-foreground'
                : isMatch
                  ? 'bg-secondary text-foreground'
                  : '';
            return (
              <span
                key={w.globalIndex}
                data-widx={w.globalIndex}
                data-start={w.start}
                data-end={w.end}
                className={className}
              >
                {w.word.trim()}{' '}
              </span>
            );
          })
        )}
      </div>

      {/* Playback bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => snapTo(adStart)}
          disabled={!hasSelection || disabled}
          className={`p-1.5 rounded ${btnGhost} disabled:opacity-40 disabled:cursor-not-allowed ${focusRing} ${touchTarget}`}
          aria-label="Snap to selection start"
          title="Snap to selection start"
        >
          <SkipBack className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={togglePlay}
          disabled={!hasSelection || disabled}
          className={`p-1.5 rounded ${btnGhost} disabled:opacity-40 disabled:cursor-not-allowed ${focusRing} ${touchTarget}`}
          aria-label={isPlaying ? 'Pause' : 'Play selection'}
        >
          {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
        </button>
        <button
          type="button"
          onClick={() => snapTo(adEnd)}
          disabled={!hasSelection || disabled}
          className={`p-1.5 rounded ${btnGhost} disabled:opacity-40 disabled:cursor-not-allowed ${focusRing} ${touchTarget}`}
          aria-label="Snap to selection end"
          title="Snap to selection end"
        >
          <SkipForward className="w-4 h-4" />
        </button>
        <SpeedMenu playbackRate={playbackRate} onChange={setPlaybackRate} disabled={disabled} />
        <span className="text-xs text-muted-foreground">
          Plays the selected span only. Selection snaps to word boundaries.
        </span>
      </div>
    </div>
  );
}

export default TextSelectionPanel;
