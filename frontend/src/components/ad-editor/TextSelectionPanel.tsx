import { useEffect, useMemo, useRef, useState } from 'react';
import { Play, Pause, SkipBack, SkipForward, Search, ChevronUp, ChevronDown } from 'lucide-react';
import {
  getOriginalSegments,
  type OriginalSegment,
  type TranscriptWord,
} from '../../api/feeds';
import { formatTime } from '../../utils/adReviewHelpers';

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
  // Playback rate is owned by the parent (same audio element drives both modes).
  playbackRate: number;
  setPlaybackRate: (r: number) => void;
}

interface FlatWord extends TranscriptWord {
  globalIndex: number; // position across all segments, for selection math
}

const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] as const;

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
  playbackRate,
  setPlaybackRate,
}: Props) {
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

  // Fetch once. The episode's words live in episode_details.original_segments_json
  // and never change after transcription, so no refetch on selection edits.
  useEffect(() => {
    let cancelled = false;
    getOriginalSegments(slug, episodeId)
      .then((res) => {
        if (cancelled) return;
        const hasWords = res.segments.some((s) => s.words && s.words.length > 0);
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

  const commitSelection = () => {
    const resolved = resolveSelection();
    if (!resolved) return;
    const first = flatWords[resolved.startIdx];
    const last = flatWords[resolved.endIdx];
    if (!first || !last) return;
    const text = flatWords
      .slice(resolved.startIdx, resolved.endIdx + 1)
      .map((w) => w.word.trim())
      .filter(Boolean)
      .join(' ');
    onSelectionChange(first.start, last.end, text);
  };

  // Commit on mouseup/touchend so drag doesn't thrash parent state. Listener
  // is scoped to the transcript root, not document, so unrelated mouseups in
  // the modal (sponsor input, etc.) don't fire commitSelection.
  useEffect(() => {
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
            className="w-full pl-8 pr-3 py-1.5 rounded-md border border-input bg-background text-foreground text-sm focus:outline-hidden focus:ring-2 focus:ring-ring"
          />
        </div>
        {matchIndices.length > 0 && (
          <>
            <span className="text-xs text-muted-foreground tabular-nums">
              {currentMatch + 1} of {matchIndices.length}
            </span>
            <button
              type="button"
              onClick={() =>
                setCurrentMatch((m) => (m - 1 + matchIndices.length) % matchIndices.length)
              }
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-accent"
              aria-label="Previous match"
            >
              <ChevronUp className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={() => setCurrentMatch((m) => (m + 1) % matchIndices.length)}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-accent"
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

      {/* Transcript. selection:bg-primary/40 keeps the active drag visible
          on mobile (default selection color is near-invisible in dark mode);
          per-word bg-primary/30 highlight below persists after the browser
          Selection clears (e.g., when focus moves to the textarea). */}
      <div
        ref={transcriptRef}
        className="bg-secondary/40 rounded-lg p-3 max-h-[40vh] overflow-y-auto text-sm leading-relaxed select-text selection:bg-primary/50 selection:text-primary-foreground"
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
          disabled={!hasSelection}
          className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label="Snap to selection start"
          title="Snap to selection start"
        >
          <SkipBack className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={togglePlay}
          disabled={!hasSelection}
          className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label={isPlaying ? 'Pause' : 'Play selection'}
        >
          {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
        </button>
        <button
          type="button"
          onClick={() => snapTo(adEnd)}
          disabled={!hasSelection}
          className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-accent disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label="Snap to selection end"
          title="Snap to selection end"
        >
          <SkipForward className="w-4 h-4" />
        </button>
        <select
          value={playbackRate}
          onChange={(e) => setPlaybackRate(Number(e.target.value))}
          className="appearance-none px-2 py-1 rounded border border-input bg-background text-foreground text-xs focus:outline-hidden focus:ring-2 focus:ring-ring"
          aria-label="Playback speed"
        >
          {PLAYBACK_RATES.map((r) => (
            <option key={r} value={r}>
              {r}x
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">
          Plays the selected span only. Selection snaps to word boundaries.
        </span>
      </div>
    </div>
  );
}

export default TextSelectionPanel;
