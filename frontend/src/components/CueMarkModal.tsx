import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Play, Pause, ZoomIn, ZoomOut, X } from 'lucide-react';
import WaveSurfer from 'wavesurfer.js';
import { usePeaks } from './ad-editor/usePeaks';
import { Pin } from './ad-editor/Pin';
import { snapToOnset } from './ad-editor/snapToOnset';
import {
  formatTime,
  getThemeWaveformColors,
  parseTimeInput,
} from '../utils/adReviewHelpers';
import {
  createCueTemplate,
  previewCueTemplate,
  type CueTemplate,
  type CueTemplateMatch,
} from '../api/cueTemplates';

// Cue template marking modal. Mirrors the AdReviewModal layout: a wavesurfer
// waveform with green START / red END pins the user drags to bracket the cue
// sound. Tuned for short stingers: 0.2 - 4s region with a deep-zoom waveform.

const MIN_REGION_SECONDS = 0.2;
const MAX_REGION_SECONDS = 4.0;
const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5] as const;
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
  onSaved: (template: CueTemplate) => void;
}

function CueMarkModal({
  podcastSlug, episodeId, episodeTitle, episodeDuration,
  initialStart, initialEnd, onClose, onSaved,
}: CueMarkModalProps) {
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

  const windowStart = 0;
  const windowEnd = totalDuration;
  const [cueStart, setCueStart] = useState(defaults.cueStart);
  const [cueEnd, setCueEnd] = useState(defaults.cueEnd);
  const [playheadTime, setPlayheadTime] = useState(0);
  // Text-input edit buffers. While not focused the inputs display the derived
  // formatTime(cueStart/cueEnd) so a pin drag or set-at-playhead is reflected
  // without a setState-in-effect sync; the buffer is seeded on focus.
  const [startInput, setStartInput] = useState(() => formatTime(defaults.cueStart));
  const [endInput, setEndInput] = useState(() => formatTime(defaults.cueEnd));
  const [startEditing, setStartEditing] = useState(false);
  const [endEditing, setEndEditing] = useState(false);
  const [label, setLabel] = useState('');
  const [zoom, setZoom] = useState(1);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState<number>(1);
  const [snapEnabled, setSnapEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewMatches, setPreviewMatches] = useState<CueTemplateMatch[] | null>(null);
  const resetTick = 0;

  const overlayRef = useRef<HTMLDivElement>(null);
  const waveformRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const cursorRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WaveSurfer | null>(null);

  const { peaks, peakResolutionMs, peaksError } = usePeaks(
    podcastSlug, episodeId, windowStart, windowEnd, resetTick,
  );

  const audioUrl = `/api/v1/feeds/${podcastSlug}/episodes/${episodeId}/original.mp3`;
  const windowDuration = Math.max(0.001, windowEnd - windowStart);

  // Close on Escape, matching the rest of the app's modal behaviour.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Snap a candidate boundary to the nearest onset when the assist is on,
  // then clamp so the region stays inside [MIN, MAX] and ordered.
  const snapStartTo = useCallback((t: number): number => {
    const snapped = snapEnabled ? snapToOnset(t, peaks, peakResolutionMs) : t;
    return Math.max(windowStart, Math.min(cueEnd - MIN_REGION_SECONDS, snapped));
  }, [snapEnabled, peaks, peakResolutionMs, cueEnd]);
  const snapEndTo = useCallback((t: number): number => {
    const snapped = snapEnabled ? snapToOnset(t, peaks, peakResolutionMs) : t;
    return Math.max(cueStart + MIN_REGION_SECONDS, Math.min(windowEnd, snapped));
  }, [snapEnabled, peaks, peakResolutionMs, cueStart, windowEnd]);

  // Mount wavesurfer when peaks arrive.
  useEffect(() => {
    if (!waveformRef.current || !peaks) return;
    const ws = WaveSurfer.create({
      container: waveformRef.current,
      height: 110,
      normalize: true,
      peaks: [peaks],
      duration: windowDuration,
      ...getThemeWaveformColors(),
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
  }, [peaks, windowStart, windowDuration]);

  // Zoom = scale wavesurfer's px/sec and grow the overlay so the pins (which
  // position by % against the overlay) stay anchored as the inner canvas
  // widens. After resize, scroll so the playhead stays centred.
  useEffect(() => {
    const ws = wsRef.current;
    const sc = scrollRef.current;
    const overlay = overlayRef.current;
    const audio = audioRef.current;
    if (!ws || !sc || !overlay) return;
    const fitPxPerSec = sc.clientWidth / Math.max(0.001, windowDuration);
    const targetPxPerSec = fitPxPerSec * zoom;
    const targetWidth = windowDuration * targetPxPerSec;
    overlay.style.minWidth = `${targetWidth}px`;
    try {
      ws.zoom(targetPxPerSec);
    } catch {
      /* ws not ready */
    }
    requestAnimationFrame(() => {
      if (!audio) return;
      const playheadX = ((audio.currentTime - windowStart) / windowDuration) * targetWidth;
      const desiredLeft = playheadX - sc.clientWidth / 2;
      sc.scrollLeft = Math.max(0, Math.min(targetWidth - sc.clientWidth, desiredLeft));
    });
  }, [zoom, peaks, windowDuration, windowStart]);

  // Audio playback wiring.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onEnd = () => setIsPlaying(false);
    audio.addEventListener('play', onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('ended', onEnd);
    return () => {
      audio.removeEventListener('play', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('ended', onEnd);
    };
  }, []);

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

  const commitStart = () => {
    const v = parseTimeInput(startInput);
    if (v != null) setCueStart(Math.max(windowStart, Math.min(cueEnd - MIN_REGION_SECONDS, v)));
    else setStartInput(formatTime(cueStart));
  };
  const commitEnd = () => {
    const v = parseTimeInput(endInput);
    if (v != null) setCueEnd(Math.max(cueStart + MIN_REGION_SECONDS, Math.min(windowEnd, v)));
    else setEndInput(formatTime(cueEnd));
  };

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  };

  const playSelection = () => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = cueStart;
    audio.play().catch(() => {});
    const stop = () => {
      if (!audioRef.current) return;
      if (audioRef.current.currentTime >= cueEnd) {
        audioRef.current.pause();
        audioRef.current.removeEventListener('timeupdate', stop);
      }
    };
    audio.addEventListener('timeupdate', stop);
  };

  const setStartAtPlayhead = useCallback(() => {
    const t = snapEnabled ? snapToOnset(playheadTime, peaks, peakResolutionMs) : playheadTime;
    let newEnd = cueEnd;
    if (t >= newEnd - MIN_REGION_SECONDS) {
      newEnd = Math.min(totalDuration, t + Math.max(MIN_REGION_SECONDS, 0.5));
    }
    if (newEnd - t > MAX_REGION_SECONDS) newEnd = t + MAX_REGION_SECONDS;
    setCueStart(Math.max(0, t));
    setCueEnd(newEnd);
  }, [playheadTime, cueEnd, totalDuration, snapEnabled, peaks, peakResolutionMs]);

  const setEndAtPlayhead = useCallback(() => {
    const t = snapEnabled ? snapToOnset(playheadTime, peaks, peakResolutionMs) : playheadTime;
    let newStart = cueStart;
    if (t <= newStart + MIN_REGION_SECONDS) {
      newStart = Math.max(0, t - Math.max(MIN_REGION_SECONDS, 0.5));
    }
    if (t - newStart > MAX_REGION_SECONDS) newStart = t - MAX_REGION_SECONDS;
    setCueStart(newStart);
    setCueEnd(Math.min(totalDuration, t));
  }, [playheadTime, cueStart, totalDuration, snapEnabled, peaks, peakResolutionMs]);

  const regionDuration = cueEnd - cueStart;
  const regionDurationValid =
    regionDuration >= MIN_REGION_SECONDS && regionDuration <= MAX_REGION_SECONDS;
  const canSave = !!label.trim() && regionDurationValid && !saving;

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      const template = await createCueTemplate(
        podcastSlug, episodeId, cueStart, cueEnd, label.trim(),
      );
      onSaved(template);
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
      const template = await createCueTemplate(
        podcastSlug, episodeId, cueStart, cueEnd, label.trim(),
      );
      const res = await previewCueTemplate(podcastSlug, episodeId, template.id);
      setPreviewMatches(res.matches);
      onSaved(template);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setPreviewing(false);
    }
  };

  const ctrlBtn = 'px-2 py-1.5 rounded border border-input hover:bg-muted text-sm';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Mark audio cue"
        className="bg-background text-foreground rounded-lg shadow-xl w-full max-w-4xl p-5 max-h-[92vh] overflow-y-auto"
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
          Drag the green and red pins to bracket the cue sound on the waveform.
          Selection must be {MIN_REGION_SECONDS} to {MAX_REGION_SECONDS} seconds.
          A cue is matched by its sound alone; if that sound also occurs outside
          ad breaks, cuts can land in the wrong place.
        </p>

        {/* Waveform + pins. Same overlay pattern as AdReviewModal. */}
        <div ref={scrollRef} className="overflow-x-auto border rounded bg-muted/30">
          <div ref={overlayRef} className="relative">
            <div ref={waveformRef} />
            {/* Amber playhead -- same visual language as the ad editor cursor so
                it is not confused with the green/red boundary pins. */}
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

        {peaksError && (
          <p className="text-sm text-destructive mt-2">
            Could not load waveform: {peaksError}
          </p>
        )}

        {/* Controls row: play / playback rate / zoom / set-at-playhead. */}
        <div className="flex flex-wrap items-center gap-2 mt-3">
          <button type="button" className={`${ctrlBtn} flex items-center gap-1`} onClick={togglePlay}>
            {isPlaying ? <Pause size={14} /> : <Play size={14} />}
            {isPlaying ? 'Pause' : 'Play'}
          </button>
          <button type="button" className={ctrlBtn} onClick={playSelection}>
            Play selection
          </button>
          <select
            value={playbackRate}
            onChange={(e) => setPlaybackRate(Number(e.target.value))}
            className="px-2 py-1.5 rounded border border-input bg-background text-sm"
            aria-label="Playback speed"
          >
            {PLAYBACK_RATES.map((r) => (
              <option key={r} value={r}>{r}x</option>
            ))}
          </select>
          <div className="flex items-center gap-1 ml-2">
            <button
              type="button"
              className="p-1.5 rounded border border-input hover:bg-muted"
              onClick={(e) => {
                const step = e.shiftKey ? 1.4 : 1.15;
                setZoom((z) => Math.max(ZOOM_MIN, z / step));
              }}
              aria-label="Zoom out"
              title="Shift+click for a coarse step"
            >
              <ZoomOut size={14} />
            </button>
            <span className="text-xs text-muted-foreground w-14 text-center font-mono">
              {zoom < 10 ? zoom.toFixed(1) : Math.round(zoom)}x
            </span>
            <button
              type="button"
              className="p-1.5 rounded border border-input hover:bg-muted"
              onClick={(e) => {
                const step = e.shiftKey ? 1.4 : 1.15;
                setZoom((z) => Math.min(ZOOM_MAX, z * step));
              }}
              aria-label="Zoom in"
              title="Shift+click for a coarse step"
            >
              <ZoomIn size={14} />
            </button>
          </div>
          <button
            type="button"
            className="px-2 py-1.5 rounded border border-emerald-500 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-500/10 text-sm"
            onClick={setStartAtPlayhead}
          >
            Set START at playhead
          </button>
          <button
            type="button"
            className="px-2 py-1.5 rounded border border-rose-500 text-rose-700 dark:text-rose-400 hover:bg-rose-500/10 text-sm"
            onClick={setEndAtPlayhead}
          >
            Set END at playhead
          </button>
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground ml-1">
            <input
              type="checkbox"
              checked={snapEnabled}
              onChange={(e) => setSnapEnabled(e.target.checked)}
            />
            Snap to onset
          </label>
          <span className="text-xs text-muted-foreground ml-auto font-mono">
            playhead {formatTime(playheadTime)} - {peakResolutionMs}ms/peak
          </span>
        </div>

        {/* Time inputs + duration + label. */}
        <div className="flex flex-wrap items-end gap-3 mt-3">
          <div>
            <label className="block text-xs text-muted-foreground" htmlFor="cue-start-in">Start</label>
            <input
              id="cue-start-in"
              type="text"
              value={startEditing ? startInput : formatTime(cueStart)}
              onFocus={() => { setStartInput(formatTime(cueStart)); setStartEditing(true); }}
              onChange={(e) => setStartInput(e.target.value)}
              onBlur={() => { commitStart(); setStartEditing(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
              className="w-24 border rounded px-2 py-1 bg-background text-sm font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground" htmlFor="cue-end-in">End</label>
            <input
              id="cue-end-in"
              type="text"
              value={endEditing ? endInput : formatTime(cueEnd)}
              onFocus={() => { setEndInput(formatTime(cueEnd)); setEndEditing(true); }}
              onChange={(e) => setEndInput(e.target.value)}
              onBlur={() => { commitEnd(); setEndEditing(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
              className="w-24 border rounded px-2 py-1 bg-background text-sm font-mono"
            />
          </div>
          <p className="text-sm">
            Duration:{' '}
            <span className={regionDurationValid ? 'font-medium' : 'font-medium text-destructive'}>
              {regionDuration.toFixed(2)}s
            </span>
          </p>
          <div className="flex-1 min-w-[220px]">
            <label className="block text-xs text-muted-foreground" htmlFor="cue-label-in">Cue label</label>
            <input
              id="cue-label-in"
              type="text"
              maxLength={80}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. break stinger"
              className="w-full border rounded px-2 py-1 bg-background text-sm"
            />
          </div>
        </div>

        {previewMatches !== null && (
          <div className="bg-muted/30 rounded p-3 mt-3">
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

        <audio ref={audioRef} src={audioUrl} preload="metadata" />

        <div className="flex justify-end gap-2 mt-4">
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
            className="px-3 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 text-sm"
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
