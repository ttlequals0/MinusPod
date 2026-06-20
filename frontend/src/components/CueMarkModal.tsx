import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';
import WaveSurfer from 'wavesurfer.js';
import { usePeaks } from './ad-editor/usePeaks';
import { Pin } from './ad-editor/Pin';
import { snapToOnset } from './ad-editor/snapToOnset';
import TransportBar from './ad-editor/TransportBar';
import ZoomControl from './ad-editor/ZoomControl';
import { primaryBtn, ctrlBtn } from './ad-editor/controlStyles';
import {
  formatTime,
  getThemeWaveformColors,
  parseTimeInput,
} from '../utils/adReviewHelpers';
import {
  createCueTemplate,
  deleteCueTemplate,
  getCueCandidates,
  previewCueTemplate,
  CUE_TYPE_OPTIONS,
  captureMaxForType,
  type CueTemplate,
  type CueTemplateMatch,
  type CueTemplateType,
  type CueCandidate,
} from '../api/cueTemplates';

// Cue template marking modal. Mirrors the AdReviewModal layout: a wavesurfer
// waveform with green START / red END pins the user drags to bracket the cue
// sound, and the same transport bar.

const DEFAULT_MIN_REGION_SECONDS = 0.2;
const DEFAULT_MAX_REGION_SECONDS = 4.0;
const SCAN_FAILED_MESSAGE = 'Recurring-sound scan failed.';
const ZOOM_MIN = 1;
// Cues are short (often <1s) and episodes can be hours long, so the
// fit-to-modal scale leaves them as a single pixel. Allow deep zoom.
const ZOOM_MAX = 500;

export interface CueMarkModalProps {
  podcastSlug: string;
  episodeId: string;
  episodeTitle: string;
  episodeDuration: number;
  initialStart?: number;
  initialEnd?: number;
  onClose: () => void;
  // Fired whenever a template is persisted (create or preview) so the list can
  // refresh.
  onSaved: (template: CueTemplate) => void;
  // Fired only on the final "Save cue" so the panel can auto-verify the new
  // cue against a few other episodes.
  onFinalSave?: (template: CueTemplate) => void;
  // Capture length bounds (the audio_cue_capture_min/max_seconds settings).
  captureMinSeconds?: number;
  captureMaxSeconds?: number;
}

function CueMarkModal({
  podcastSlug, episodeId, episodeTitle, episodeDuration,
  initialStart, initialEnd, onClose, onSaved, onFinalSave,
  captureMinSeconds = DEFAULT_MIN_REGION_SECONDS,
  captureMaxSeconds = DEFAULT_MAX_REGION_SECONDS,
}: CueMarkModalProps) {
  const MIN_REGION_SECONDS = captureMinSeconds;
  // Window always covers the entire episode -- zoom widens the inner
  // wavesurfer canvas inside an overflow-x scroller, with the scroll
  // following the playhead, so the user always sees the whole episode at
  // 1x and zooms into the playhead position.
  const totalDuration = Math.max(0.001, episodeDuration);
  const defaults = useMemo(() => ({
    cueStart: typeof initialStart === 'number' ? initialStart : 0,
    cueEnd: typeof initialEnd === 'number'
      ? initialEnd
      : Math.min(totalDuration, 1.0),
  }), [initialStart, initialEnd, totalDuration]);

  const [cueStart, setCueStart] = useState(defaults.cueStart);
  const [cueEnd, setCueEnd] = useState(defaults.cueEnd);
  const [playheadTime, setPlayheadTime] = useState(0);
  // Time-input edit buffers, kept in sync with cueStart/cueEnd ONLY while the
  // input is not focused (mirrors AdReviewModal), so typing is never stomped
  // mid-edit and a pin drag still updates the displayed value.
  const [startInput, setStartInput] = useState(() => formatTime(defaults.cueStart));
  const [endInput, setEndInput] = useState(() => formatTime(defaults.cueEnd));
  const [cueType, setCueType] = useState<CueTemplateType>('ad_break_boundary');
  // Capture ceiling follows the cue type: intro/outro stingers get a longer
  // allowance than ad-break dings (mirrors the server-side bound).
  const MAX_REGION_SECONDS = useMemo(
    () => captureMaxForType(cueType, captureMaxSeconds),
    [cueType, captureMaxSeconds],
  );
  const [zoom, setZoom] = useState(1);
  // Windowed rendering: zoom narrows the rendered time-span (kept to about one
  // screen-width at any zoom) instead of widening a giant canvas. wavesurfer
  // caps its render at ~16000px and leaves everything past that blank, so the
  // old "whole episode, zoom widens the canvas" approach went blank at the far
  // end. At 1x the window is the whole episode. Deferred so dragging the zoom
  // slider / scrubber stays responsive while the windowed peaks re-fetch.
  const [windowCenter, setWindowCenter] = useState(() => (defaults.cueStart + defaults.cueEnd) / 2);
  const deferredZoom = useDeferredValue(zoom);
  const deferredCenter = useDeferredValue(windowCenter);
  const { windowStart, windowEnd } = useMemo(() => {
    const winDur = Math.min(totalDuration, Math.max(0.5, totalDuration / Math.max(1, deferredZoom)));
    let start = Math.max(0, Math.min(totalDuration - winDur, deferredCenter - winDur / 2));
    if (!Number.isFinite(start)) start = 0;
    return { windowStart: start, windowEnd: start + winDur };
  }, [deferredZoom, deferredCenter, totalDuration]);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState<number>(1);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewMatches, setPreviewMatches] = useState<CueTemplateMatch[] | null>(null);
  // Recurring-sound candidates (on-demand). Raw loud spots were too noisy and
  // the scan is slow, so it runs only when the user asks for it.
  const [candidates, setCandidates] = useState<CueCandidate[] | null>(null);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const candidatePollRef = useRef<number | null>(null);
  // Bumped on each new scan and on unmount, so a stale fetch's resolution bails
  // instead of scheduling a poll or calling setState on a dead component.
  const candidateRunRef = useRef(0);
  const resetTick = 0;

  const dialogRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const waveformRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const scrubberRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const startInputRef = useRef<HTMLInputElement | null>(null);
  const endInputRef = useRef<HTMLInputElement | null>(null);
  // The timeupdate listener that stops "Play selection" at the end pin. Held in
  // a ref so a manual transport action cancels it -- otherwise it lingers and
  // pauses unrelated playback the next time the playhead crosses the end pin.
  const selectionStopRef = useRef<(() => void) | null>(null);

  // Fetch the whole episode's peaks ONCE (stable), then slice the current
  // window out client-side. Re-fetching per window would null `peaks` on every
  // zoom/pan tick, flashing the pins and waveform; slicing keeps the loaded
  // peaks stable so only the rendered slice changes.
  const { peaks, peakResolutionMs, peaksError } = usePeaks(
    podcastSlug, episodeId, 0, totalDuration, resetTick,
  );

  const audioUrl = `/api/v1/feeds/${podcastSlug}/episodes/${episodeId}/original.mp3`;
  const windowDuration = Math.max(0.001, windowEnd - windowStart);

  // Peaks for just the visible window (a slice of the full-episode peaks).
  const windowPeaks = useMemo(() => {
    if (!peaks) return null;
    const bucket = peakResolutionMs / 1000;
    if (!(bucket > 0)) return peaks;
    const startIdx = Math.max(0, Math.floor(windowStart / bucket));
    const endIdx = Math.min(peaks.length, Math.ceil(windowEnd / bucket));
    return endIdx > startIdx ? peaks.slice(startIdx, endIdx) : peaks;
  }, [peaks, peakResolutionMs, windowStart, windowEnd]);

  // Close on Escape, matching the rest of the app's modal behaviour.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Move focus into the dialog on open so keyboard and screen-reader users land
  // inside it.
  useEffect(() => { dialogRef.current?.focus(); }, []);

  // Keep the time-input buffers in sync with the bounds when the field is not
  // focused (pin drag / set-at-playhead), without stomping an in-progress edit.
  useEffect(() => {
    if (document.activeElement !== startInputRef.current) setStartInput(formatTime(cueStart));
  }, [cueStart]);
  useEffect(() => {
    if (document.activeElement !== endInputRef.current) setEndInput(formatTime(cueEnd));
  }, [cueEnd]);

  const seekTo = useCallback((t: number) => {
    const audio = audioRef.current;
    const clamped = Math.max(0, Math.min(totalDuration, t));
    if (audio) audio.currentTime = clamped;
    // Recenter the rendered window on the jump target so it stays visible when
    // zoomed in (a no-op at 1x where the window is the whole episode).
    setWindowCenter(clamped);
  }, [totalDuration]);

  // Full-episode scrubber: drag to pan the zoomed window across the episode.
  const panToClientX = useCallback((clientX: number) => {
    const el = scrubberRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    setWindowCenter(frac * totalDuration);
  }, [totalDuration]);
  const onScrubberPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    panToClientX(e.clientX);
    const move = (ev: PointerEvent) => panToClientX(ev.clientX);
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
  };

  // The scan runs in a background thread server-side; poll until it reports
  // done. `force` re-runs after an error rather than re-reading the cache.
  const findCandidates = useCallback((force = false) => {
    if (candidatePollRef.current) {
      clearTimeout(candidatePollRef.current);
      candidatePollRef.current = null;
    }
    const run = ++candidateRunRef.current;
    setCandidatesLoading(true);
    setCandidatesError(null);
    const step = (forceThis: boolean) => {
      getCueCandidates(podcastSlug, episodeId, forceThis)
        .then((res) => {
          if (run !== candidateRunRef.current) return; // superseded or unmounted
          if (res.status === 'scanning') {
            candidatePollRef.current = window.setTimeout(() => step(false), 3000);
            return;
          }
          setCandidates(res.candidates);
          setCandidatesLoading(false);
          if (res.status === 'error') {
            setCandidatesError(res.error || SCAN_FAILED_MESSAGE);
          }
        })
        .catch(() => {
          if (run !== candidateRunRef.current) return;
          setCandidates([]);
          setCandidatesLoading(false);
          setCandidatesError(SCAN_FAILED_MESSAGE);
        });
    };
    step(force);
  }, [podcastSlug, episodeId]);

  // Invalidate any in-flight scan and stop polling if the modal closes mid-scan.
  useEffect(() => () => {
    candidateRunRef.current++;
    if (candidatePollRef.current) {
      clearTimeout(candidatePollRef.current);
      candidatePollRef.current = null;
    }
  }, []);

  // Snap an ABSOLUTE time to the nearest onset. The peaks are for the current
  // window [windowStart, windowEnd], so convert to/from window-relative before
  // indexing them (snapToOnset assumes peaks[0] == time 0).
  const snapAbs = useCallback((t: number): number => {
    if (!snapEnabled) return t;
    return windowStart + snapToOnset(t - windowStart, windowPeaks, peakResolutionMs);
  }, [snapEnabled, windowStart, windowPeaks, peakResolutionMs]);

  // Snap a candidate boundary to the nearest onset when the assist is on,
  // then clamp so the region stays inside [MIN, MAX] and ordered.
  const snapStartTo = useCallback((t: number): number => {
    return Math.max(0, Math.min(cueEnd - MIN_REGION_SECONDS, snapAbs(t)));
  }, [snapAbs, cueEnd, MIN_REGION_SECONDS]);
  const snapEndTo = useCallback((t: number): number => {
    return Math.max(cueStart + MIN_REGION_SECONDS, Math.min(totalDuration, snapAbs(t)));
  }, [snapAbs, cueStart, totalDuration, MIN_REGION_SECONDS]);

  // Mount wavesurfer for the current window's peak slice. Re-renders when the
  // window changes (zoom/pan); the slice keeps the canvas width at one screen
  // so wavesurfer never has to render past its ~16000px cap.
  useEffect(() => {
    if (!waveformRef.current || !windowPeaks) return;
    // Color the bars with the theme accent (primary) so the capture waveform is
    // vividly themed -- matching how the ad editor's waveform reads in each
    // theme rather than the muted grey wavesurfer would otherwise show at rest.
    const themeWave = getThemeWaveformColors();
    const ws = WaveSurfer.create({
      container: waveformRef.current,
      height: 110,
      normalize: true,
      peaks: [windowPeaks],
      duration: windowDuration,
      waveColor: themeWave.progressColor,
      progressColor: themeWave.progressColor,
      // Render our own amber playhead overlay instead of wavesurfer's built-in
      // cursor; the built-in one is easy to confuse with a pin.
      cursorColor: 'transparent',
      mediaControls: false,
      interact: true,
      barWidth: 2,
      barGap: 1,
    });
    wsRef.current = ws;

    // Seek the audio element (which drives playback) when the user clicks the
    // waveform. wavesurfer 7 emits relative time in seconds (0 .. duration).
    ws.on('interaction', (relTime: number) => {
      const audio = audioRef.current;
      if (!audio) return;
      audio.currentTime = windowStart + relTime;
    });

    return () => {
      ws.destroy();
      wsRef.current = null;
    };
  }, [windowPeaks, windowStart, windowDuration]);

  useEffect(() => {
    const audio = audioRef.current;
    if (audio) audio.playbackRate = playbackRate;
  }, [playbackRate]);

  // Drive the playhead overlay (+ time readout) from the audio element. Also
  // push currentTime into wavesurfer so its progress fill tracks real playback.
  useEffect(() => {
    let raf = 0;
    let lastTime = -1;
    const tick = () => {
      const audio = audioRef.current;
      const cursor = cursorRef.current;
      if (audio && cursor) {
        const t = audio.currentTime;
        setPlayheadTime(t);
        const rel = (t - windowStart) / windowDuration;
        if (rel >= 0 && rel <= 1) {
          cursor.style.left = `${rel * 100}%`;
          cursor.style.display = '';
        } else {
          cursor.style.display = 'none';
        }
        const ws = wsRef.current;
        if (ws && t !== lastTime) {
          try {
            // setTime() is wavesurfer 7's hard seek; updates currentTime
            // without firing 'interaction', so no feedback loop.
            ws.setTime(Math.max(0, t - windowStart));
          } catch {
            /* ws torn down mid-update */
          }
          lastTime = t;
        }
      }
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [windowStart, windowDuration]);

  // Commit clamps only to absolute episode bounds (not cross-field), so editing
  // one field never stomps the other; start < end is enforced at Save.
  const commitStart = () => {
    const v = parseTimeInput(startInput);
    if (v == null) { setStartInput(formatTime(cueStart)); return; }
    const clamped = Math.max(0, Math.min(totalDuration, v));
    setCueStart(clamped);
    setStartInput(formatTime(clamped));
  };
  const commitEnd = () => {
    const v = parseTimeInput(endInput);
    if (v == null) { setEndInput(formatTime(cueEnd)); return; }
    const clamped = Math.max(0, Math.min(totalDuration, v));
    setCueEnd(clamped);
    setEndInput(formatTime(clamped));
  };

  const clearSelectionStop = useCallback(() => {
    if (selectionStopRef.current) selectionStopRef.current();
    selectionStopRef.current = null;
  }, []);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    clearSelectionStop();
    if (audio.paused) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  };

  const seekRelative = (delta: number) => {
    const audio = audioRef.current;
    if (audio) audio.currentTime = Math.max(0, Math.min(totalDuration, audio.currentTime + delta));
  };
  const stopPlayback = () => {
    const audio = audioRef.current;
    if (!audio) return;
    clearSelectionStop();
    audio.pause();
    audio.currentTime = cueStart;
  };

  const playSelection = () => {
    const audio = audioRef.current;
    if (!audio) return;
    clearSelectionStop();
    // Seek then play only once metadata is loaded -- a pre-load seek is dropped
    // by Chrome/Safari and would play from the episode start.
    const begin = () => {
      audio.currentTime = cueStart;
      const stop = () => {
        const a = audioRef.current;
        if (a && a.currentTime >= cueEnd) {
          a.pause();
          clearSelectionStop();
        }
      };
      audio.addEventListener('timeupdate', stop);
      selectionStopRef.current = () => audio.removeEventListener('timeupdate', stop);
      audio.play().catch(() => {});
    };
    if (audio.readyState >= 1) {
      begin();
    } else {
      audio.addEventListener('loadedmetadata', begin, { once: true });
      selectionStopRef.current = () => audio.removeEventListener('loadedmetadata', begin);
    }
  };

  // Set-at-playhead only enforces the MIN gap (so the region stays ordered and
  // non-zero); it deliberately does NOT clamp to the max. Length is validated
  // at save, where the type-specific ceiling applies -- clamping here snapped a
  // long intro/outro back to the ad-break 4s default mid-edit.
  const setStartAtPlayhead = useCallback(() => {
    const t = snapAbs(playheadTime);
    let newEnd = cueEnd;
    if (t >= newEnd - MIN_REGION_SECONDS) {
      newEnd = Math.min(totalDuration, t + Math.max(MIN_REGION_SECONDS, 0.5));
    }
    setCueStart(Math.max(0, t));
    setCueEnd(newEnd);
  }, [snapAbs, playheadTime, cueEnd, totalDuration, MIN_REGION_SECONDS]);

  const setEndAtPlayhead = useCallback(() => {
    const t = snapAbs(playheadTime);
    let newStart = cueStart;
    if (t <= newStart + MIN_REGION_SECONDS) {
      newStart = Math.max(0, t - Math.max(MIN_REGION_SECONDS, 0.5));
    }
    setCueStart(newStart);
    setCueEnd(Math.min(totalDuration, t));
  }, [snapAbs, playheadTime, cueStart, totalDuration, MIN_REGION_SECONDS]);

  const regionDuration = cueEnd - cueStart;
  const regionDurationValid =
    regionDuration >= MIN_REGION_SECONDS && regionDuration <= MAX_REGION_SECONDS;
  const canSave = regionDurationValid && !saving;

  // The last persisted template for the current selection. Save-and-preview and
  // Save reuse it when the bounds and type have not changed, so previewing
  // before saving does not leave a duplicate cue behind.
  const persistedRef = useRef<{ start: number; end: number; cueType: CueTemplateType; template: CueTemplate } | null>(null);

  const ensureTemplate = useCallback(async (): Promise<CueTemplate> => {
    const prev = persistedRef.current;
    if (
      prev && prev.cueType === cueType &&
      Math.abs(prev.start - cueStart) < 0.001 &&
      Math.abs(prev.end - cueEnd) < 0.001
    ) {
      return prev.template;
    }
    const template = await createCueTemplate(podcastSlug, episodeId, cueStart, cueEnd, cueType);
    // The bracket or type changed since the last save/preview; drop the now
    // superseded template so a preview-then-rebracket flow leaves only the
    // latest cue rather than accumulating drafts.
    if (prev) {
      try { await deleteCueTemplate(prev.template.id); } catch { /* best effort */ }
    }
    persistedRef.current = { start: cueStart, end: cueEnd, cueType, template };
    onSaved(template);
    return template;
  }, [cueStart, cueEnd, cueType, podcastSlug, episodeId, onSaved]);

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      const template = await ensureTemplate();
      onFinalSave?.(template);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handlePreview = async () => {
    if (!canSave) return;
    setPreviewing(true);
    setError(null);
    try {
      const template = await ensureTemplate();
      const res = await previewCueTemplate(podcastSlug, episodeId, template.id);
      setPreviewMatches(res.matches);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setPreviewing(false);
    }
  };

  const fieldCls =
    'rounded-lg border border-input bg-background text-foreground ' +
    'focus:outline-hidden focus:ring-2 focus:ring-ring';
  const inCue = playheadTime >= cueStart && playheadTime <= cueEnd;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 backdrop-blur-sm p-4"
      onMouseDown={(e) => {
        // Data-entry modal: never auto-close on an outside click (an accidental
        // tap would lose the bracket). Only X / Cancel / Escape close it.
        if (e.target !== e.currentTarget) return;
      }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Mark audio cue"
        className="bg-card text-foreground rounded-lg border border-border shadow-2xl w-full max-w-4xl p-4 sm:p-5 max-h-[92vh] overflow-y-auto focus:outline-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            <h2 className="text-lg font-semibold">Mark audio cue</h2>
            <p className="text-sm text-muted-foreground truncate max-w-2xl">
              {episodeTitle}
            </p>
          </div>
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <p className="text-sm text-muted-foreground mb-3">
          Drag the pins to bracket the cue ({MIN_REGION_SECONDS}-{MAX_REGION_SECONDS}s).
        </p>

        {/* Waveform + pins. Same overlay pattern as AdReviewModal. */}
        <div ref={scrollRef} className="overflow-hidden border border-border rounded-lg bg-secondary/40 min-h-[140px]">
          <div ref={overlayRef} className="relative">
            <div ref={waveformRef} />
            {/* Selected-cue region highlight -- same amber fill the ad editor
                uses for its selection window. Below the markers/pins/playhead. */}
            {peaks && windowDuration > 0 && (
              <div
                className="absolute inset-y-0 z-[4] pointer-events-none"
                style={{
                  left: `${((cueStart - windowStart) / windowDuration) * 100}%`,
                  width: `${Math.max(0, ((cueEnd - cueStart) / windowDuration) * 100)}%`,
                  backgroundColor: 'rgba(245, 158, 11, 0.18)',
                }}
                aria-hidden
              />
            )}
            {/* Amber playhead, same as the ad editor cursor. */}
            <div
              ref={cursorRef}
              className="absolute inset-y-0 -translate-x-1/2 z-20 pointer-events-none"
              style={{ left: '0%', display: 'none' }}
              aria-hidden
            >
              <div className="absolute top-1 left-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 border-white bg-amber-500 shadow-md" />
              <div className="absolute -top-5 left-1/2 -translate-x-1/2 px-1.5 py-0.5 rounded bg-amber-500 text-white text-[10px] font-bold whitespace-nowrap shadow-md">
                {formatTime(playheadTime)}
              </div>
              <div className="absolute top-[20px] bottom-0 left-1/2 -translate-x-1/2 w-0.5 bg-amber-500 shadow-[0_0_4px_rgba(245,158,11,0.8)]" />
            </div>
            {/* Recurring-sound markers: the sounds that repeat across the
                episode (the cue candidates). Click to jump; full-height and
                tinted so they read clearly under the boundary pins. */}
            {(candidates ?? []).map((c) => {
              const rel = (c.start - windowStart) / windowDuration;
              if (rel < 0 || rel > 1) return null;
              return (
                <button
                  key={`${c.start}-${c.end}`}
                  type="button"
                  onClick={() => seekTo(c.start)}
                  title={`Recurs ${c.count}x, first at ${formatTime(c.start)} - click to jump`}
                  aria-label={`Jump to recurring sound at ${formatTime(c.start)}`}
                  className="absolute inset-y-0 -translate-x-1/2 z-[5] w-3 cursor-pointer group"
                  style={{ left: `${rel * 100}%` }}
                >
                  <span className="block mx-auto h-full w-0.5 bg-sky-500/60 group-hover:bg-sky-500" />
                  <span className="absolute top-0 left-1/2 -translate-x-1/2 px-1 rounded-b bg-sky-500 text-white text-[9px] font-bold leading-tight">
                    {c.count}x
                  </span>
                </button>
              );
            })}
            {/* Pins. */}
            {peaks && (
              <>
                <Pin
                  kind="start"
                  boundary={cueStart}
                  windowStart={windowStart}
                  windowDuration={windowDuration}
                  containerRef={overlayRef}
                  onChange={setCueStart}
                  onDragEnd={() => setCueStart((s) => snapStartTo(s))}
                  otherBoundary={cueEnd}
                  minSeparation={MIN_REGION_SECONDS}
                />
                <Pin
                  kind="end"
                  boundary={cueEnd}
                  windowStart={windowStart}
                  windowDuration={windowDuration}
                  containerRef={overlayRef}
                  onChange={setCueEnd}
                  onDragEnd={() => setCueEnd((e) => snapEndTo(e))}
                  otherBoundary={cueStart}
                  minSeparation={MIN_REGION_SECONDS}
                />
              </>
            )}
          </div>
        </div>

        {/* Full-episode scrubber: shows where the zoomed window sits and lets
            the user pan across the whole episode (only useful when zoomed). */}
        {zoom > 1 && totalDuration > 0 && (
          <div className="mt-2">
            <div
              ref={scrubberRef}
              role="slider"
              aria-label="Episode position"
              aria-valuemin={0}
              aria-valuemax={Math.round(totalDuration)}
              aria-valuenow={Math.round(windowCenter)}
              tabIndex={0}
              onPointerDown={onScrubberPointerDown}
              className="relative h-3 rounded-full bg-background border border-border cursor-pointer touch-none focus:outline-hidden focus:ring-2 focus:ring-ring"
            >
              <div
                className="absolute inset-y-0 bg-primary/30 rounded-full pointer-events-none"
                style={{
                  left: `${(windowStart / totalDuration) * 100}%`,
                  width: `${Math.max(1, ((windowEnd - windowStart) / totalDuration) * 100)}%`,
                }}
                aria-hidden
              />
              <div
                className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-1.5 h-1.5 rounded-full bg-amber-500 pointer-events-none"
                style={{ left: `${(playheadTime / totalDuration) * 100}%` }}
                aria-hidden
              />
            </div>
            <div className="flex justify-between text-[10px] text-muted-foreground mt-0.5 tabular-nums">
              <span>{formatTime(windowStart)}</span>
              <span>showing {formatTime(windowEnd - windowStart)}</span>
              <span>{formatTime(windowEnd)}</span>
            </div>
          </div>
        )}

        {peaksError && (
          <p className="text-sm text-destructive mt-2">
            Could not load waveform: {peaksError}
          </p>
        )}

        {/* Find recurring sounds (on-demand -- the scan decodes the episode). */}
        <div className="flex flex-wrap items-center gap-2 mt-2">
          <button
            type="button"
            className={ctrlBtn}
            onClick={() => findCandidates(!!candidatesError)}
            disabled={candidatesLoading}
          >
            {candidatesLoading
              ? 'Finding recurring sounds...'
              : candidatesError ? 'Try again' : 'Find recurring sounds'}
          </button>
          {candidatesError && !candidatesLoading && (
            <span className="text-xs text-destructive">{candidatesError}</span>
          )}
          {candidates !== null && !candidatesLoading && !candidatesError && (
            <span className="text-xs text-muted-foreground">
              {candidates.length === 0
                ? 'No recurring sounds found.'
                : `${candidates.length} recurring sound${candidates.length === 1 ? '' : 's'} (markers) - tap one to jump.`}
            </span>
          )}
        </div>

        {/* Zoom -- shared with the "Add new ad" editor. */}
        <ZoomControl
          value={zoom}
          min={ZOOM_MIN}
          max={ZOOM_MAX}
          step={1}
          onChange={setZoom}
          onZoomIn={() => setZoom((z) => Math.min(ZOOM_MAX, +(z * 1.5).toFixed(2)))}
          onZoomOut={() => setZoom((z) => Math.max(ZOOM_MIN, +(z / 1.5).toFixed(2)))}
        />

        {/* Playback transport -- shared with the "Add new ad" editor. */}
        <TransportBar
          isPlaying={isPlaying}
          onTogglePlay={togglePlay}
          onSeekToStart={() => seekTo(cueStart)}
          onSeekToEnd={() => seekTo(cueEnd)}
          onSeekRelative={seekRelative}
          onStop={stopPlayback}
          playbackRate={playbackRate}
          onPlaybackRateChange={setPlaybackRate}
          currentTime={playheadTime}
          selectionDuration={regionDuration}
          inSelection={inCue}
          selectionLabel="in cue"
          onPlaySelection={playSelection}
        />

        {/* Cue-specific controls: snap to onset + set edge at playhead. */}
        <div className="flex flex-wrap items-center gap-2 mt-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input type="checkbox" className="accent-primary" checked={snapEnabled} onChange={(e) => setSnapEnabled(e.target.checked)} />
            Snap to onset
          </label>
          <button type="button" className={`flex-1 sm:flex-none ${ctrlBtn} text-emerald-500 whitespace-nowrap`} onClick={setStartAtPlayhead}>
            <span className="sm:hidden">Set START</span>
            <span className="hidden sm:inline">Set START at playhead</span>
          </button>
          <button type="button" className={`flex-1 sm:flex-none ${ctrlBtn} text-rose-500 whitespace-nowrap`} onClick={setEndAtPlayhead}>
            <span className="sm:hidden">Set END</span>
            <span className="hidden sm:inline">Set END at playhead</span>
          </button>
        </div>

        {/* Time inputs + duration + label. */}
        <div className="flex flex-wrap items-end gap-3 mt-3">
          <div>
            <label className="block text-xs text-muted-foreground" htmlFor="cue-start-in">Start</label>
            <input
              id="cue-start-in"
              ref={startInputRef}
              type="text"
              inputMode="decimal"
              value={startInput}
              onChange={(e) => setStartInput(e.target.value)}
              onBlur={commitStart}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
                else if (e.key === 'Escape') { e.preventDefault(); setStartInput(formatTime(cueStart)); (e.target as HTMLInputElement).blur(); }
              }}
              className={`w-24 px-3 py-1.5 ${fieldCls} text-sm font-mono text-emerald-500`}
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground" htmlFor="cue-end-in">End</label>
            <input
              id="cue-end-in"
              ref={endInputRef}
              type="text"
              inputMode="decimal"
              value={endInput}
              onChange={(e) => setEndInput(e.target.value)}
              onBlur={commitEnd}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
                else if (e.key === 'Escape') { e.preventDefault(); setEndInput(formatTime(cueEnd)); (e.target as HTMLInputElement).blur(); }
              }}
              className={`w-24 px-3 py-1.5 ${fieldCls} text-sm font-mono text-rose-500`}
            />
          </div>
          <p className="text-sm">
            Duration:{' '}
            <span className={regionDurationValid ? 'font-medium' : 'font-medium text-destructive'}>
              {regionDuration.toFixed(2)}s
            </span>
            {!regionDurationValid && (
              <span className="ml-2 text-xs text-destructive">
                {regionDuration <= 0
                  ? 'Start must be before end'
                  : regionDuration < MIN_REGION_SECONDS
                    ? `Min ${MIN_REGION_SECONDS}s`
                    : `Max ${MAX_REGION_SECONDS}s`}
              </span>
            )}
          </p>
          <div className="flex-1 min-w-[220px]">
            <label className="block text-xs text-muted-foreground" htmlFor="cue-type-in">Cue type</label>
            <select
              id="cue-type-in"
              value={cueType}
              onChange={(e) => setCueType(e.target.value as CueTemplateType)}
              className={`w-full px-3 py-1.5 ${fieldCls} text-sm`}
            >
              {CUE_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>

        {previewMatches !== null && (
          <div className="bg-secondary/40 rounded-lg p-3 mt-3">
            <p className="text-sm font-medium mb-1">
              Preview matches on this episode: {previewMatches.length}
            </p>
            {previewMatches.length > 0 && (
              <ul className="text-xs grid grid-cols-2 sm:grid-cols-3 gap-1 max-h-32 overflow-y-auto">
                {previewMatches.slice(0, 30).map((m, i) => (
                  <li key={i} className="font-mono">
                    {formatTime(m.start)} (score {m.score.toFixed(2)})
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {error && <p className="text-sm text-destructive mt-3">{error}</p>}

        <audio
          ref={audioRef}
          src={audioUrl}
          preload="metadata"
          onPlay={() => setIsPlaying(true)}
          onPause={() => setIsPlaying(false)}
          onEnded={() => setIsPlaying(false)}
        />

        <p className="text-xs text-muted-foreground mt-3 border-t border-border pt-3">
          Matched by sound alone - if it also plays outside ad breaks, cuts can land wrong.
        </p>

        <div className="flex flex-col sm:flex-row sm:justify-end gap-2 mt-4">
          <button
            type="button"
            className={ctrlBtn}
            onClick={onClose}
            disabled={saving || previewing}
          >
            Cancel
          </button>
          <button
            type="button"
            className={ctrlBtn}
            onClick={handlePreview}
            disabled={!canSave || previewing}
          >
            {previewing ? 'Previewing...' : 'Save and preview'}
          </button>
          <button
            type="button"
            className={`px-4 py-1.5 rounded-lg ${primaryBtn} text-sm`}
            onClick={handleSave}
            disabled={!canSave}
          >
            {saving ? 'Saving...' : 'Save cue'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default CueMarkModal;
