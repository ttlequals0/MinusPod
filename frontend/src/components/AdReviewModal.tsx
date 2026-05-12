import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Play, Pause, SkipBack, SkipForward, Rewind, FastForward, Square,
  ZoomIn, ZoomOut,
} from 'lucide-react';
import WaveSurfer from 'wavesurfer.js';
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js';
import { getEpisodePeaks, getTranscriptSpan } from '../api/feeds';

// Shape used by the per-episode AdEditor: enough to render the waveform
// editor for a single detected ad and submit a correction back. Matches
// what PR #204's inbox passed in, but renamed to reflect the per-episode
// scope of v2.2.0.
export interface AdReviewItem {
  podcastSlug: string;
  episodeId: string;
  start: number;
  end: number;
  sponsor: string | null;
  reason: string | null;
  confidence: number | null;
  detectionStage: string | null;
  patternId: number | null;
  correctedBounds: { start: number; end: number } | null;
}

export interface AdReviewSubmit {
  kind: 'confirm' | 'reject' | 'adjust';
  adjustedStart?: number;
  adjustedEnd?: number;
  sponsor?: string;
}

export interface AdCreateSubmit {
  kind: 'create';
  start: number;
  end: number;
  sponsor: string;
  textTemplate: string;
  scope: 'podcast' | 'global';
  reason: string;
}

interface Props {
  item: AdReviewItem;
  onClose: () => void;
  // Called when the user confirms / rejects / adjusts. The host owns the
  // queue (or single-item) lifecycle; this component just emits intent.
  onSubmit: (s: AdReviewSubmit) => void;
  // Skip = advance UI without mutating DB.
  onSkip: () => void;
  // Hides the "& Next" button text when there's no queue.
  hasNext?: boolean;
  // Audio mode: 'processed' plays the post-cut file, 'original' plays the
  // retained pre-cut file. Original is forced in create mode (you can't
  // mark a new ad on already-cut audio).
  audioMode?: 'processed' | 'original';
  onAudioModeChange?: (m: 'processed' | 'original') => void;
  hasOriginal?: boolean;
  processedAudioUrl?: string;
  // Optional: total episode duration so create mode can default
  // end-of-selection to the end of the file.
  episodeDuration?: number;
  // 'review' (default) is the existing flow. 'create' switches into
  // net-new-ad mode against the original audio: empty boundaries,
  // editable sponsor + text_template fields, and a different submit
  // signature via onCreate.
  mode?: 'review' | 'create';
  onCreate?: (s: AdCreateSubmit) => void;
  // Optional: surface a "+ Add new ad" entry inside the modal so the
  // user can switch into create mode without closing the modal first.
  onAddNew?: () => void;
}

const CONTEXT_SECONDS = 30;
// Cap the default visible window. Some heuristic detections (notably
// post-roll) flag dozens of minutes as a single "ad", which would make
// the default fit-zoom view useless (whole episode squeezed into one
// screen). Six minutes is enough to set ad start with context; user can
// always expand via the +1m buttons or wheel-zoom in.
const DEFAULT_MAX_WINDOW_SECONDS = 360;
const WINDOW_STEP_SECONDS = 60;
// 100ms buckets — 4× fewer peaks than the prior 50ms default. Still plenty
// of detail to see speech vs. silence at any reasonable zoom level, and
// shaves the JSON payload + canvas-render cost on first mount roughly 4×.
const PEAK_RESOLUTION_MS = 100;
const MIN_WINDOW_PAD = 10;
const MIN_AD_DURATION = 1.0;
const PLAY_WHILE_DRAG_KEY = 'minuspod.adInbox.playWhileDragging';

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return '0:00';
  const sign = seconds < 0 ? '-' : '';
  const total = Math.abs(seconds);
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${sign}${m}:${s.toFixed(1).padStart(4, '0')}`;
}

function loadPlayWhileDragging(): boolean {
  try {
    return localStorage.getItem(PLAY_WHILE_DRAG_KEY) === '1';
  } catch {
    return false;
  }
}
function savePlayWhileDragging(v: boolean) {
  try {
    localStorage.setItem(PLAY_WHILE_DRAG_KEY, v ? '1' : '0');
  } catch {
    /* private mode etc */
  }
}

// ----------------------------------------------------------------------
// Pin: vertical drag handle above the waveform that controls the
// corresponding ad boundary. Pins ARE the user's drag interface — the
// wavesurfer region is decorative (drag/resize disabled on it).

interface PinProps {
  kind: 'start' | 'end';
  boundary: number;
  windowStart: number;
  windowDuration: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onChange: (next: number) => void;
  // Called while drag is in progress so we can scrub audio if enabled.
  onDragMove?: (next: number) => void;
  onDragStart?: () => void;
  onDragEnd?: () => void;
  otherBoundary: number;        // for min-separation clamp
}

function Pin({
  kind, boundary, windowStart, windowDuration, containerRef,
  onChange, onDragMove, onDragStart, onDragEnd, otherBoundary,
}: PinProps) {
  const [dragging, setDragging] = useState(false);

  const relX = (boundary - windowStart) / windowDuration;
  // Tolerate a tiny bit outside [0, 1] — happens routinely on post-roll ads
  // where the LLM places adEnd a hair past where the audio file actually
  // ends, which makes relX = 1.0001 or so. Without slop the END pin
  // disappears entirely.
  const visible = relX >= -0.02 && relX <= 1.02;
  const leftPct = Math.max(0, Math.min(1, relX)) * 100;

  const isStart = kind === 'start';
  const color = isStart ? 'bg-emerald-500' : 'bg-rose-500';
  const ringColor = isStart ? 'ring-emerald-500/40' : 'ring-rose-500/40';
  const labelText = isStart ? 'START' : 'END';

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const container = containerRef.current;
    if (!container) return;

    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDragging(true);
    onDragStart?.();

    const rect = container.getBoundingClientRect();

    const computeBoundary = (clientX: number): number => {
      const xPct = (clientX - rect.left) / rect.width;
      const clampedPct = Math.max(0, Math.min(1, xPct));
      const t = windowStart + clampedPct * windowDuration;
      // Min-separation: never let start cross end (and vice-versa).
      if (isStart) return Math.min(t, otherBoundary - MIN_AD_DURATION);
      return Math.max(t, otherBoundary + MIN_AD_DURATION);
    };

    const handleMove = (ev: PointerEvent) => {
      const next = computeBoundary(ev.clientX);
      onChange(next);
      onDragMove?.(next);
    };
    const handleUp = (ev: PointerEvent) => {
      const next = computeBoundary(ev.clientX);
      onChange(next);
      setDragging(false);
      onDragEnd?.();
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleUp);
    };

    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleUp);
  };

  if (!visible) return null;

  // Compact pin: small colored circle pinhead, thin stem. The label
  // (with time) only shows when the pin is being dragged or hovered —
  // when idle, just the circle is visible. Negative top offsets are
  // avoided so the pinhead doesn't get clipped by the parent's
  // overflow-x scrollbox.

  return (
    <div
      onPointerDown={onPointerDown}
      style={{
        left: `${leftPct}%`,
        touchAction: 'none',
      }}
      className={`group absolute inset-y-0 -translate-x-1/2 z-10 cursor-ew-resize select-none ${
        dragging ? 'cursor-grabbing' : ''
      }`}
      role="slider"
      aria-label={`${labelText} pin · ${formatTime(boundary)}`}
      aria-valuenow={Math.round(boundary * 10) / 10}
    >
      {/* Compact circle pinhead at top. */}
      <div
        className={`absolute top-1 left-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 border-white ${color} shadow-md ${
          dragging ? `ring-4 ${ringColor} scale-125` : ''
        } transition-transform`}
      />
      {/* Time label — only visible while dragging or on hover. */}
      <div
        className={`absolute -top-5 left-1/2 -translate-x-1/2 px-1.5 py-0.5 rounded ${color} text-white text-[10px] font-bold tracking-wider whitespace-nowrap shadow-md transition-opacity duration-100 pointer-events-none ${
          dragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
        }`}
      >
        {labelText} {formatTime(boundary)}
      </div>
      {/* Stem — runs from just below the circle to the bottom. */}
      <div
        className={`absolute top-[20px] bottom-0 left-1/2 -translate-x-1/2 w-0.5 ${color} ${
          dragging ? 'opacity-100' : 'opacity-80'
        }`}
      />
      {/* Touch target — wraps the whole pin column for easy mobile grab. */}
      <div
        className="absolute inset-y-0 -inset-x-4"
        style={{ touchAction: 'none' }}
      />
    </div>
  );
}

// ----------------------------------------------------------------------
// Playhead cursor — ref-driven DOM updates from the RAF loop, NOT React
// state, so position can update at full 60fps without re-rendering the
// whole modal tree (which fights wavesurfer + the regions plugin).
// Position is read from the parent component's RAF loop via the
// imperative handle returned by Cursor.

// ----------------------------------------------------------------------

function AdReviewModal({
  item, onClose, onSubmit, onSkip, hasNext = false,
  audioMode = 'original', onAudioModeChange, hasOriginal = true,
  processedAudioUrl, episodeDuration,
  mode = 'review', onCreate, onAddNew,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);   // waveform host
  const overlayRef = useRef<HTMLDivElement>(null);     // relative wrapper around waveform + pins
  const scrollContainerRef = useRef<HTMLDivElement>(null); // overflow-x-auto wrapper
  const cursorRef = useRef<HTMLDivElement>(null);      // playhead, position-updated from RAF
  const audioRef = useRef<HTMLAudioElement>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const regionsRef = useRef<ReturnType<typeof RegionsPlugin.create> | null>(null);
  const adRegionRef = useRef<ReturnType<RegionsPlugin['addRegion']> | null>(null);

  // Defaults derived from the original detection — used by Reset.
  // Create mode has no detection; default to a window at episode start.
  const defaults = useMemo(() => {
    if (mode === 'create') {
      const fullDuration = Math.max(0, episodeDuration ?? 0);
      const initialEnd = Math.min(DEFAULT_MAX_WINDOW_SECONDS, fullDuration || DEFAULT_MAX_WINDOW_SECONDS);
      return {
        windowStart: 0,
        windowEnd: initialEnd,
        adStart: 0,
        adEnd: Math.min(60, initialEnd),
      };
    }
    const windowStart = Math.max(0, item.start - CONTEXT_SECONDS);
    const naturalEnd = item.end + CONTEXT_SECONDS;
    const cappedEnd = windowStart + DEFAULT_MAX_WINDOW_SECONDS;
    return {
      windowStart,
      // Cap the visible default to DEFAULT_MAX_WINDOW_SECONDS so a heuristic
      // post-roll that spans the rest of the episode doesn't render the
      // whole thing at fit-zoom. User can still see further via +1m or by
      // zooming.
      windowEnd: Math.min(naturalEnd, cappedEnd),
      adStart: (item.correctedBounds ?? item).start,
      adEnd: (item.correctedBounds ?? item).end,
    };
  }, [mode, episodeDuration, item.start, item.end, item.correctedBounds]);

  const [windowStart, setWindowStart] = useState(defaults.windowStart);
  const [windowEnd, setWindowEnd] = useState(defaults.windowEnd);
  const [adStart, setAdStart] = useState(defaults.adStart);
  const [adEnd, setAdEnd] = useState(defaults.adEnd);

  const [peaks, setPeaks] = useState<number[] | null>(null);
  // Resolution actually used by the server. May be coarser than requested
  // when the window is very long (audio_peaks auto-scales to keep the
  // payload bounded). Drives effective-duration math below.
  const [peakResolutionMs, setPeakResolutionMs] = useState<number>(PEAK_RESOLUTION_MS);
  const [peaksError, setPeaksError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  // Zoom is a multiplier of "fit" — 1 = fit-to-container, 2 = 2× zoomed in, etc.
  const [zoom, setZoom] = useState(1);
  const ZOOM_MIN = 1;
  const ZOOM_MAX = 20;
  // Bumped by resetView to force a clean wavesurfer rebuild (and re-fetch
  // of peaks). Belt-and-suspenders so Reset always lands a known-good
  // state regardless of which states actually changed.
  const [resetTick, setResetTick] = useState(0);
  const [playWhileDrag, setPlayWhileDrag] = useState<boolean>(loadPlayWhileDragging);
  const wasPlayingBeforeDragRef = useRef(false);
  // Save the playhead position before a pin drag (with playWhileDrag) so
  // we can put it back where the user was listening, instead of stranding
  // it at the new pin position.
  const positionBeforePinDragRef = useRef<number | null>(null);
  const [sponsorInput, setSponsorInput] = useState(item.sponsor ?? '');
  const [showSponsorPrompt, setShowSponsorPrompt] = useState(!item.sponsor);
  // Create-mode only: a text template the user can edit before submit.
  // Left empty here so the host can wire a transcript-span fetch into it.
  const [textTemplateInput, setTextTemplateInput] = useState('');
  const [scopeInput, setScopeInput] = useState<'podcast' | 'global'>('podcast');
  const [reasonInput, setReasonInput] = useState('');

  // Create mode is always against original audio (you can't mark a new ad
  // on already-cut audio). Review mode honors the parent's audioMode.
  const effectiveAudioMode = mode === 'create' ? 'original' : audioMode;
  const audioUrl =
    effectiveAudioMode === 'original' || !processedAudioUrl
      ? `/api/v1/feeds/${item.podcastSlug}/episodes/${item.episodeId}/original.mp3`
      : processedAudioUrl;
  // The user-requested window. May extend past the actual end of the file
  // for post-roll ads — ffmpeg silently truncates and returns fewer peaks.
  const requestedWindowDuration = useMemo(
    () => Math.max(0.001, windowEnd - windowStart),
    [windowStart, windowEnd],
  );
  // The window we actually have peaks for. When peaks are loaded, derive
  // duration from peak count × resolution so visual positioning matches
  // the audio that exists. Pins / cursor / region all use this.
  const windowDuration = useMemo(() => {
    if (peaks && peaks.length > 0) {
      return Math.max(0.001, (peaks.length * peakResolutionMs) / 1000);
    }
    return requestedWindowDuration;
  }, [peaks, peakResolutionMs, requestedWindowDuration]);
  // Effective end = start + actual covered duration. Used in the displayed
  // time labels so the user sees the same window the pins / waveform are
  // actually showing — important for post-roll ads whose requested window
  // extends past the file end.
  const effectiveWindowEnd = useMemo(
    () => windowStart + windowDuration,
    [windowStart, windowDuration],
  );

  // ------------------------------------------------------------------
  // Fetch peaks whenever window changes. The setState pair here clears
  // stale data before the new fetch lands so the waveform can't show
  // peaks from the previous window for a frame. React 19's strict
  // set-state-in-effect rule fires here; the pattern is correct
  // because we're synchronizing UI state with an in-flight external
  // fetch.
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPeaksError(null);
    setPeaks(null);
    getEpisodePeaks(item.podcastSlug, item.episodeId, windowStart, windowEnd, PEAK_RESOLUTION_MS)
      .then((res) => {
        if (cancelled) return;
        setPeaks(res.peaks);
        setPeakResolutionMs(res.resolutionMs || PEAK_RESOLUTION_MS);
      })
      .catch((e) => {
        if (!cancelled) setPeaksError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [item.podcastSlug, item.episodeId, windowStart, windowEnd, resetTick]);

  // ------------------------------------------------------------------
  // Create mode only: auto-populate text template from the transcript
  // span the user has selected. Debounced; only fills when empty so we
  // don't clobber edits.
  useEffect(() => {
    if (mode !== 'create') return;
    if (!(adStart >= 0 && adEnd > adStart)) return;
    const t = setTimeout(() => {
      getTranscriptSpan(item.podcastSlug, item.episodeId, adStart, adEnd)
        .then((res) => {
          setTextTemplateInput((prev) => (prev.length === 0 ? res.text : prev));
        })
        .catch(() => {});
    }, 250);
    return () => clearTimeout(t);
  }, [mode, item.podcastSlug, item.episodeId, adStart, adEnd]);

  // ------------------------------------------------------------------
  // Mount wavesurfer when peaks/window arrive. Region is decorative —
  // drag/resize disabled because the Pin components own that interaction.
  useEffect(() => {
    if (!containerRef.current || !peaks) return;

    wsRef.current?.destroy();
    wsRef.current = null;

    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: containerRef.current,
      peaks: [peaks],
      duration: windowDuration,
      waveColor: '#64748b',
      progressColor: '#22d3ee',
      cursorColor: 'transparent',  // we render our own playhead — see <Cursor /> below
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      height: 120,
      interact: true,
      plugins: [regions],
    });

    regionsRef.current = regions;
    wsRef.current = ws;

    // wavesurfer 7 mounts its scroll-container inside an *open shadow DOM*
    // attached to containerRef. When minPxPerSec*duration > parent width
    // it grows an overflow-x: auto scrollbar — duplicate of our own outer
    // wrapper. Walk the shadow tree and force every element with overflow
    // styling to be visible. Use setProperty(important) because wavesurfer
    // sets these as inline styles which a plain assignment can't override.
    const stripInnerScroll = () => {
      // Wavesurfer 7 mounts a div as containerRef's first child and
      // attaches an open shadow root TO THAT DIV (not to containerRef
      // itself). Walk through both layers.
      const host = containerRef.current;
      if (!host) return;
      const wsHost = host.firstElementChild as HTMLElement | null;
      if (wsHost) {
        wsHost.style.setProperty('overflow', 'visible', 'important');
      }
      const shadow = wsHost?.shadowRoot ?? null;
      const roots: (ShadowRoot | HTMLElement)[] = shadow ? [shadow, host] : [host];
      for (const root of roots) {
        root.querySelectorAll('*').forEach((el) => {
          const e = el as HTMLElement;
          if (e.style?.overflow || e.style?.overflowX || e.style?.overflowY) {
            e.style.setProperty('overflow', 'visible', 'important');
            e.style.setProperty('overflow-x', 'visible', 'important');
            e.style.setProperty('overflow-y', 'visible', 'important');
          }
        });
      }
      // Belt-and-suspenders: also inject a !important rule into the
      // shadow root so any post-render restyling is overridden too.
      if (shadow && !shadow.querySelector('style[data-no-inner-scroll]')) {
        const style = document.createElement('style');
        style.setAttribute('data-no-inner-scroll', '1');
        style.textContent = `
          ::part(scroll), div { overflow: visible !important; overflow-x: visible !important; overflow-y: visible !important; }
        `;
        shadow.appendChild(style);
      }
    };
    stripInnerScroll();
    ws.on('redraw', stripInnerScroll);
    ws.on('ready', stripInnerScroll);
    // Backstop: a couple of deferred calls catch any post-init style
    // applied after our initial pass (some wavesurfer versions style
    // their wrapper asynchronously after the first render frame).
    requestAnimationFrame(stripInnerScroll);
    setTimeout(stripInnerScroll, 100);

    const region = regions.addRegion({
      start: Math.max(0, adStart - windowStart),
      end: Math.min(windowDuration, adEnd - windowStart),
      color: 'rgba(245, 158, 11, 0.18)',
      drag: false,
      resize: false,
    });
    adRegionRef.current = region;

    // Stop the region from swallowing pointer events — clicks anywhere in
    // the waveform (including inside the ad band) should pass through to
    // wavesurfer's seek and to our cursor scrub overlay.
    const regionEl = (region as unknown as { element?: HTMLElement }).element;
    if (regionEl) {
      regionEl.style.pointerEvents = 'none';
    }

    ws.on('interaction', (relTime: number) => {
      if (audioRef.current) {
        audioRef.current.currentTime = windowStart + relTime;
      }
    });

    return () => {
      ws.destroy();
      wsRef.current = null;
      regionsRef.current = null;
      adRegionRef.current = null;
    };
    // Intentionally only re-mount on window/peaks change, not on bound moves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peaks, windowDuration, windowStart]);

  // Push zoom changes into wavesurfer AND resize the pin overlay so the
  // pins stay anchored to the right moments when zoomed. The pin overlay's
  // width must match the waveform's actual rendered width — pins use
  // `left: %` against the overlay's box.
  useEffect(() => {
    const ws = wsRef.current;
    const sc = scrollContainerRef.current;
    const overlay = overlayRef.current;
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
    // Kill the inner scrollbar wavesurfer (re)applies after zoom.
    requestAnimationFrame(() => {
      const host = containerRef.current;
      const root = host?.shadowRoot ?? host;
      if (!root) return;
      root.querySelectorAll('*').forEach((el) => {
        const e = el as HTMLElement;
        if (e.style?.overflow || e.style?.overflowX || e.style?.overflowY) {
          e.style.setProperty('overflow', 'visible', 'important');
          e.style.setProperty('overflow-x', 'visible', 'important');
          e.style.setProperty('overflow-y', 'visible', 'important');
        }
      });
    });
  }, [zoom, peaks, windowDuration]);

  // Reflect ad-boundary state into the existing region without rebuilding ws.
  useEffect(() => {
    const region = adRegionRef.current;
    if (!region) return;
    try {
      region.setOptions({
        start: Math.max(0, adStart - windowStart),
        end: Math.min(windowDuration, adEnd - windowStart),
      });
    } catch {
      /* region torn down mid-update */
    }
  }, [adStart, adEnd, windowStart, windowDuration]);

  // ------------------------------------------------------------------
  // Cursor sync: <audio> drives the cursor position via direct DOM update
  // (ref-based, no React re-render). React state is only updated ~10×/s
  // for the transport time readout — full-rate state updates would
  // re-render the whole modal at 60fps and stutter the cursor.
  useEffect(() => {
    let raf = 0;
    let lastReportedRoundedTime = -1;
    const loop = () => {
      const audio = audioRef.current;
      const cursor = cursorRef.current;
      if (audio && cursor) {
        const t = audio.currentTime;
        const rel = (t - windowStart) / windowDuration;
        if (Number.isFinite(rel) && rel >= 0 && rel <= 1) {
          cursor.style.left = `${rel * 100}%`;
          cursor.style.display = '';
        } else {
          cursor.style.display = 'none';
        }
        // Throttled state push for the transport readout.
        const rounded = Math.round(t * 10) / 10;
        if (rounded !== lastReportedRoundedTime) {
          lastReportedRoundedTime = rounded;
          setCurrentTime(t);
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [windowStart, windowDuration]);

  // ------------------------------------------------------------------
  // Seed audio.currentTime to a sensible spot near the ad on first
  // metadata-loaded event AND whenever the active item changes. Without
  // this, audio plays from t=0 (the start of the file) when the user hits
  // Play — for a post-roll ad whose window is at e.g. 6980-7200s, the
  // cursor would never enter the visible window. Snap to ad-start so the
  // user lands on the ad. We seed to (adStart - 2) for a tiny pre-roll.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const seek = () => {
      const target = Math.max(0, adStart - 2);
      // Don't fight the user if they've already moved the playhead.
      if (audio.currentTime < 0.1) {
        audio.currentTime = target;
      }
    };
    if (audio.readyState >= 1 /* HAVE_METADATA */) {
      seek();
    } else {
      audio.addEventListener('loadedmetadata', seek, { once: true });
      return () => audio.removeEventListener('loadedmetadata', seek);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.podcastSlug, item.episodeId, item.start, item.end, resetTick]);

  // ------------------------------------------------------------------
  // Audio playback.
  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      // Don't snap the playhead — let the user listen anywhere they want.
      // Use the SkipBack button (or J / J on the ad start pin) to return
      // to the ad start.
      audio.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
    } else {
      audio.pause();
      setIsPlaying(false);
    }
  };

  const seekTo = (t: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, t);
  };
  const seekRelative = (delta: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, audio.currentTime + delta);
  };
  const seekToAdStart = () => seekTo(adStart);
  const seekToAdEnd = () => seekTo(adEnd);
  const stopPlayback = () => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    audio.currentTime = adStart;
    setIsPlaying(false);
  };

  // ------------------------------------------------------------------
  // Pin drag → optional audio scrub. Plays a tiny preview at the pin's
  // current time so the user can hear what they're aligning to.
  const onPinDragStart = () => {
    const audio = audioRef.current;
    if (!audio) return;
    wasPlayingBeforeDragRef.current = !audio.paused;
    positionBeforePinDragRef.current = audio.currentTime;
    if (playWhileDrag) {
      audio.play().catch(() => {});
    } else if (!audio.paused) {
      audio.pause();
    }
  };
  const onPinDragMove = (next: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = next;
  };
  const onPinDragEnd = () => {
    const audio = audioRef.current;
    if (!audio) return;
    // Put the playhead back where the user was before they grabbed a
    // pin. Adjusting an ad boundary shouldn't permanently move the
    // listening position.
    if (positionBeforePinDragRef.current !== null) {
      audio.currentTime = positionBeforePinDragRef.current;
      positionBeforePinDragRef.current = null;
    }
    // Restore the play/pause state from before the drag started, so
    // adjusting a boundary never feels like it pressed Pause.
    if (wasPlayingBeforeDragRef.current) {
      audio.play()
        .then(() => setIsPlaying(true))
        .catch(() => setIsPlaying(false));
    } else {
      audio.pause();
      setIsPlaying(false);
    }
  };

  // ------------------------------------------------------------------
  // Window expand / shrink / reset.
  const expandBack = () => setWindowStart((s) => Math.max(0, s - WINDOW_STEP_SECONDS));
  const expandForward = () => setWindowEnd((e) => e + WINDOW_STEP_SECONDS);
  const shrinkBack = () =>
    setWindowStart((s) => Math.min(adStart - MIN_WINDOW_PAD, s + WINDOW_STEP_SECONDS));
  const shrinkForward = () =>
    setWindowEnd((e) => Math.max(adEnd + MIN_WINDOW_PAD, e - WINDOW_STEP_SECONDS));
  const resetView = () => {
    setWindowStart(defaults.windowStart);
    setWindowEnd(defaults.windowEnd);
    setAdStart(defaults.adStart);
    setAdEnd(defaults.adEnd);
    setZoom(1);
    setPeaks(null);                        // force a re-fetch + rebuild
    setResetTick((n) => n + 1);
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = defaults.adStart;
      setIsPlaying(false);
    }
  };

  const zoomIn = () => setZoom((z) => Math.min(ZOOM_MAX, +(z * 1.5).toFixed(2)));
  const zoomOut = () => setZoom((z) => Math.max(ZOOM_MIN, +(z / 1.5).toFixed(2)));

  // Mouse-wheel zoom on the waveform: Ctrl/Shift wheel zooms,
  // bare wheel still scrolls horizontally (browser default in overflow-x-auto).
  // We intercept ALL wheel events on the scroll container so the user doesn't
  // need a modifier key — feels more natural for an audio-editing surface.
  const onWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    // Only act on vertical wheel (deltaY); leave horizontal wheel alone so
    // trackpad horizontal panning still scrolls the waveform.
    if (Math.abs(e.deltaY) < Math.abs(e.deltaX)) return;
    e.preventDefault();
    // Zoom around the cursor: capture the time at the cursor before, then
    // restore the same time at the same cursor x after zoom by adjusting scroll.
    const sc = scrollContainerRef.current;
    if (!sc) return;
    const rect = sc.getBoundingClientRect();
    const cursorX = e.clientX - rect.left + sc.scrollLeft;
    const fitPxPerSec = rect.width / Math.max(0.001, windowDuration);
    const oldPxPerSec = fitPxPerSec * zoom;
    const cursorTime = cursorX / Math.max(0.001, oldPxPerSec);
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const nextZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, +(zoom * factor).toFixed(3)));
    setZoom(nextZoom);
    // Re-anchor the cursor: schedule scroll adjustment after layout updates.
    requestAnimationFrame(() => {
      const sc2 = scrollContainerRef.current;
      if (!sc2) return;
      const newPxPerSec = fitPxPerSec * nextZoom;
      const newCursorX = cursorTime * newPxPerSec;
      sc2.scrollLeft = newCursorX - (e.clientX - rect.left);
    });
  };

  // ------------------------------------------------------------------
  // Submission — the host owns the actual API call (so it can also
  // refresh the surrounding episode view, navigate, etc.); we just emit.
  const [isBusy, setIsBusy] = useState(false);

  const boundariesMoved =
    Math.abs(adStart - item.start) > 0.05 || Math.abs(adEnd - item.end) > 0.05;

  const handleConfirm = async () => {
    if (isBusy) return;
    setIsBusy(true);
    try {
      onSubmit(
        boundariesMoved
          ? {
              kind: 'adjust',
              adjustedStart: adStart,
              adjustedEnd: adEnd,
              sponsor: sponsorInput.trim() || undefined,
            }
          : { kind: 'confirm', sponsor: sponsorInput.trim() || undefined }
      );
    } finally {
      setIsBusy(false);
    }
  };

  const handleReject = async () => {
    if (isBusy) return;
    setIsBusy(true);
    try {
      onSubmit({ kind: 'reject' });
    } finally {
      setIsBusy(false);
    }
  };

  // ------------------------------------------------------------------
  // Hotkeys
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField =
        target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA');
      if (inField && e.key !== 'Escape') return;

      if (e.key === 'Escape') { e.preventDefault(); onClose(); return; }
      if (e.key === ' ')      { e.preventDefault(); togglePlay(); return; }
      if (e.key === ',')      { e.preventDefault(); expandBack(); return; }
      if (e.key === '.')      { e.preventDefault(); expandForward(); return; }
      if (e.key === 'c' || e.key === 'C') { e.preventDefault(); if (!isBusy) handleConfirm(); return; }
      if (e.key === 'r' || e.key === 'R') { e.preventDefault(); if (!isBusy) handleReject(); return; }
      if (e.key === 's' || e.key === 'S') { e.preventDefault(); if (!isBusy) onSkip(); return; }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        const audio = audioRef.current;
        if (!audio) return;
        e.preventDefault();
        const delta = e.shiftKey ? 5 : 1;
        audio.currentTime += e.key === 'ArrowRight' ? delta : -delta;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBusy, onClose, sponsorInput, adStart, adEnd, item.start, item.end]);

  // ------------------------------------------------------------------
  // Style helpers — explicit hover treatments so buttons clearly
  // highlight on mouseover instead of looking washed out.

  const primaryBtn =
    'bg-primary text-primary-foreground transition-all ' +
    'hover:bg-primary hover:ring-2 hover:ring-primary hover:ring-offset-2 hover:ring-offset-card ' +
    'disabled:opacity-50 disabled:cursor-not-allowed';
  const destructiveBtn =
    'bg-destructive text-destructive-foreground transition-all ' +
    'hover:bg-destructive hover:ring-2 hover:ring-destructive hover:ring-offset-2 hover:ring-offset-card ' +
    'disabled:opacity-50 disabled:cursor-not-allowed';
  const ghostBtn =
    'border border-border text-foreground bg-card transition-colors ' +
    'hover:bg-accent hover:text-accent-foreground hover:border-foreground/30 ' +
    'disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-card disabled:hover:text-foreground disabled:hover:border-border';

  // ------------------------------------------------------------------

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="bg-card rounded-lg border border-border w-full max-w-4xl max-h-[90vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header — title + actions on top row, detection metadata below. */}
        <div className="px-6 py-4 border-b border-border space-y-2">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <h2 className="text-lg font-semibold text-foreground truncate min-w-0 flex-1">
              {mode === 'create' ? 'Add new ad' : 'Detected ad'}
            </h2>
            <div className="flex items-center gap-2 flex-wrap">
              {/* Processed / Original toggle. Hidden in create mode (always original). */}
              {mode === 'review' && onAudioModeChange && (
                <div className="inline-flex rounded-md border border-input overflow-hidden" role="group">
                  <button
                    type="button"
                    onClick={() => onAudioModeChange('processed')}
                    className={`px-2 py-1 text-xs transition-colors ${
                      audioMode === 'processed'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-background text-muted-foreground hover:bg-secondary'
                    }`}
                    title="Play the post-cut audio"
                  >
                    Processed
                  </button>
                  <button
                    type="button"
                    disabled={!hasOriginal}
                    onClick={() => onAudioModeChange('original')}
                    className={`px-2 py-1 text-xs transition-colors ${
                      audioMode === 'original' && hasOriginal
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-background text-muted-foreground hover:bg-secondary'
                    } ${!hasOriginal ? 'opacity-40 cursor-not-allowed' : ''}`}
                    title={hasOriginal
                      ? 'Play the pre-cut audio at the ads original timestamps'
                      : 'Original audio not retained for this episode'}
                  >
                    Original
                  </button>
                </div>
              )}
              {/* + Add new ad — only in review mode, only when host wires it. */}
              {mode === 'review' && onAddNew && (
                <button
                  type="button"
                  onClick={onAddNew}
                  className="px-2 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  + Add new ad
                </button>
              )}
              <button
                onClick={onClose}
                className="p-1 rounded text-muted-foreground transition-colors hover:text-foreground hover:bg-accent"
                aria-label="Close"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
          {/* Detection metadata sits on its own row below the action chrome,
              so it can't push the toggle/close into a wrap on narrow screens. */}
          {mode === 'review' && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>Stage: {item.detectionStage ?? '-'}</span>
              {item.confidence !== null && <span>Confidence: {Math.round(item.confidence * 100)}%</span>}
              {item.patternId !== null && <span>Pattern #{item.patternId}</span>}
              {item.reason && <span className="italic truncate max-w-full" title={item.reason}>{item.reason}</span>}
            </div>
          )}
        </div>

        {/* Window controls + reset */}
        <div className="px-6 pt-4 flex items-center justify-between gap-2 flex-wrap text-xs text-muted-foreground tabular-nums">
          <div className="flex items-center gap-2">
            <button type="button" onClick={expandBack}
              className={`px-2 py-1 rounded ${ghostBtn}`}
              title="Expand window 1 min earlier ( , )">« +1m</button>
            <button type="button" onClick={shrinkBack}
              disabled={windowStart >= adStart - MIN_WINDOW_PAD - WINDOW_STEP_SECONDS}
              className={`px-2 py-1 rounded ${ghostBtn}`}
              title="Shrink window from the left">» −1m</button>
            <span className="ml-2">{formatTime(windowStart)}</span>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={playWhileDrag}
                onChange={(e) => {
                  setPlayWhileDrag(e.target.checked);
                  savePlayWhileDragging(e.target.checked);
                }}
                className="accent-primary"
              />
              <span>Play audio while dragging pin</span>
            </label>
            <button type="button" onClick={resetView}
              className={`px-2 py-1 rounded ${ghostBtn}`}
              title="Reset waveform window + ad bounds to defaults">↻ Reset</button>
          </div>
          <div className="flex items-center gap-2">
            <span>{formatTime(effectiveWindowEnd)}</span>
            <button type="button" onClick={shrinkForward}
              disabled={windowEnd <= adEnd + MIN_WINDOW_PAD + WINDOW_STEP_SECONDS}
              className={`ml-2 px-2 py-1 rounded ${ghostBtn}`}
              title="Shrink window from the right">« −1m</button>
            <button type="button" onClick={expandForward}
              className={`px-2 py-1 rounded ${ghostBtn}`}
              title="Expand window 1 min later ( . )">+1m »</button>
          </div>
        </div>

        {/* Waveform + pin overlay */}
        <div className="px-6 py-4">
          <div className="bg-secondary/40 rounded-lg p-3 min-h-[180px]">
            {peaksError ? (
              <p className="text-sm text-destructive">Failed to load waveform: {peaksError}</p>
            ) : !peaks ? (
              <p className="text-sm text-muted-foreground">Loading waveform…</p>
            ) : (
              <div
                ref={scrollContainerRef}
                onWheel={onWheel}
                className="overflow-x-auto"
              >
                <div className="relative min-w-full" ref={overlayRef}>
                  {/* Header strip — gives the pinheads a place to live INSIDE
                      the overlay's box (so they aren't clipped by the
                      enclosing overflow-x-auto scroll container). */}
                  <div className="h-9" />
                  {/* Pins live in the same horizontal coordinate system as
                      the waveform host (overlayRef). When zoom > 1, wavesurfer
                      widens its canvas — the relative wrapper grows with it,
                      so pin `left: %` keeps tracking the right time. */}
                  <Pin
                    kind="start"
                    boundary={adStart}
                    windowStart={windowStart}
                    windowDuration={windowDuration}
                    containerRef={overlayRef}
                    otherBoundary={adEnd}
                    onChange={setAdStart}
                    onDragStart={onPinDragStart}
                    onDragMove={playWhileDrag ? onPinDragMove : undefined}
                    onDragEnd={onPinDragEnd}
                  />
                  <Pin
                    kind="end"
                    boundary={adEnd}
                    windowStart={windowStart}
                    windowDuration={windowDuration}
                    containerRef={overlayRef}
                    otherBoundary={adStart}
                    onChange={setAdEnd}
                    onDragStart={onPinDragStart}
                    onDragMove={playWhileDrag ? onPinDragMove : undefined}
                    onDragEnd={onPinDragEnd}
                  />
                  <div
                    ref={cursorRef}
                    className="group/cursor absolute inset-y-0 -translate-x-1/2 z-20"
                    style={{ left: '0%', display: 'none', touchAction: 'none' }}
                    aria-hidden
                    onPointerDown={(e) => {
                      // Drag the cursor pinhead to scrub the audio. Scrub
                      // is bounded by the visible window, NOT the ad
                      // boundary — user can listen anywhere in context.
                      const overlay = overlayRef.current;
                      const audio = audioRef.current;
                      if (!overlay || !audio) return;
                      e.preventDefault();
                      e.stopPropagation();
                      (e.target as HTMLElement).setPointerCapture(e.pointerId);
                      const rect = overlay.getBoundingClientRect();
                      // AUDIO PLAYS DURING SCRUB. Start playback if it was
                      // paused so the user always hears what they're
                      // pointing at; just keep seeking on each pointermove.
                      if (audio.paused) {
                        audio.play()
                          .then(() => setIsPlaying(true))
                          .catch(() => {});
                      }
                      const compute = (clientX: number) => {
                        const xPct = (clientX - rect.left) / rect.width;
                        const clamped = Math.max(0, Math.min(1, xPct));
                        return windowStart + clamped * windowDuration;
                      };
                      const onMove = (ev: PointerEvent) => {
                        audio.currentTime = compute(ev.clientX);
                      };
                      const onUp = (ev: PointerEvent) => {
                        audio.currentTime = compute(ev.clientX);
                        window.removeEventListener('pointermove', onMove);
                        window.removeEventListener('pointerup', onUp);
                        window.removeEventListener('pointercancel', onUp);
                      };
                      window.addEventListener('pointermove', onMove);
                      window.addEventListener('pointerup', onUp);
                      window.addEventListener('pointercancel', onUp);
                    }}
                  >
                    {/* Compact circle pinhead at top. */}
                    <div className="absolute top-1 left-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 border-white bg-amber-500 shadow-md cursor-ew-resize" />
                    {/* Time label — hover or while moving. */}
                    <div className="absolute -top-5 left-1/2 -translate-x-1/2 px-1.5 py-0.5 rounded bg-amber-500 text-white text-[10px] font-bold whitespace-nowrap shadow-md transition-opacity duration-100 pointer-events-none opacity-0 group-hover/cursor:opacity-100">
                      ▶ {formatTime(currentTime)}
                    </div>
                    {/* Stem */}
                    <div className="absolute top-[20px] bottom-0 left-1/2 -translate-x-1/2 w-0.5 bg-amber-500 shadow-[0_0_4px_rgba(245,158,11,0.8)] pointer-events-none" />
                    {/* Wider hit area */}
                    <div className="absolute inset-y-0 -inset-x-4 cursor-ew-resize" />
                  </div>
                  <div ref={containerRef} className="w-full" />
                </div>
              </div>
            )}
          </div>

          {/* Zoom slider */}
          <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
            <button type="button" onClick={zoomOut}
              disabled={zoom <= ZOOM_MIN + 0.01}
              className={`p-1.5 rounded ${ghostBtn}`}
              title="Zoom out (mouse wheel down)">
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
            <input
              type="range"
              min={ZOOM_MIN}
              max={ZOOM_MAX}
              step={0.1}
              value={zoom}
              onChange={(e) => setZoom(Number(e.target.value))}
              className="flex-1 accent-primary"
              title="Zoom"
            />
            <button type="button" onClick={zoomIn}
              disabled={zoom >= ZOOM_MAX - 0.01}
              className={`p-1.5 rounded ${ghostBtn}`}
              title="Zoom in (mouse wheel up)">
              <ZoomIn className="w-3.5 h-3.5" />
            </button>
            <span className="tabular-nums w-10 text-right">{zoom.toFixed(1)}×</span>
          </div>

          {/* Boundaries readout */}
          <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-muted-foreground tabular-nums">
            <span>
              Selection:{' '}
              <span className="text-emerald-500 font-medium">{formatTime(adStart)}</span>{' '}
              –{' '}
              <span className="text-rose-500 font-medium">{formatTime(adEnd)}</span>{' '}
              <span className="text-xs">({Math.round((adEnd - adStart) * 10) / 10}s)</span>
            </span>
            {boundariesMoved && (
              <span className="text-xs text-amber-500">
                (originally {formatTime(item.start)} – {formatTime(item.end)})
              </span>
            )}
          </div>

          <audio
            ref={audioRef}
            src={audioUrl}
            preload="metadata"
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            onEnded={() => setIsPlaying(false)}
          />

          {/* Transport bar */}
          <div className="mt-3 flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-secondary/50 border border-border flex-wrap">
            <div className="flex items-center gap-1">
              <button type="button" onClick={seekToAdStart}
                className={`p-2 rounded ${ghostBtn}`}
                title="Jump to START pin">
                <SkipBack className="w-4 h-4" />
              </button>
              <button type="button" onClick={() => seekRelative(-10)}
                className={`p-2 rounded ${ghostBtn}`}
                title="Back 10s">
                <Rewind className="w-4 h-4" />
              </button>
              <button type="button" onClick={togglePlay}
                className={`p-2 rounded-full ${primaryBtn}`}
                title="Play / pause (Space)">
                {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
              </button>
              <button type="button" onClick={() => seekRelative(10)}
                className={`p-2 rounded ${ghostBtn}`}
                title="Forward 10s">
                <FastForward className="w-4 h-4" />
              </button>
              <button type="button" onClick={seekToAdEnd}
                className={`p-2 rounded ${ghostBtn}`}
                title="Jump to END pin">
                <SkipForward className="w-4 h-4" />
              </button>
              <button type="button" onClick={stopPlayback}
                className={`p-2 rounded ${ghostBtn}`}
                title="Stop (pause + return to START)">
                <Square className="w-4 h-4" />
              </button>
            </div>
            <div className="flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
              <span className="text-foreground">{formatTime(currentTime)}</span>
              <span>/</span>
              <span>{formatTime(adEnd - adStart)} selection</span>
              {currentTime >= adStart && currentTime <= adEnd && (
                <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 text-[10px] font-semibold uppercase tracking-wider">
                  inside ad
                </span>
              )}
            </div>
          </div>

          <div className="mt-2 text-xs text-muted-foreground">
            Drag the <span className="text-emerald-500 font-semibold">START</span> /{' '}
            <span className="text-rose-500 font-semibold">END</span> pins above the waveform.{' '}
            <kbd>Space</kbd> play • <kbd>,</kbd>/<kbd>.</kbd> expand window • mouse-wheel to zoom • <kbd>C</kbd> confirm • <kbd>R</kbd> reject • <kbd>S</kbd> skip
          </div>
        </div>

        {/* Sponsor prompt + (in create mode) text-template + scope */}
        {mode === 'create' ? (
          <div className="px-6 py-4 border-t border-border bg-secondary/30 space-y-3">
            <label className="block text-sm font-medium text-foreground">
              Sponsor name
              <input
                type="text" value={sponsorInput}
                onChange={(e) => setSponsorInput(e.target.value)}
                placeholder="e.g. BetterHelp, Squarespace, Progressive"
                className="mt-1 w-full px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-sm"
              />
            </label>
            <label className="block text-sm font-medium text-foreground">
              Text template
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                (auto-populated from the transcript; edit before save)
              </span>
              <textarea
                value={textTemplateInput}
                onChange={(e) => setTextTemplateInput(e.target.value)}
                rows={4}
                className="mt-1 w-full px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-xs font-mono"
              />
              <div className={`text-xs mt-1 ${textTemplateInput.trim().length < 50 ? 'text-destructive' : 'text-muted-foreground'}`}>
                {textTemplateInput.trim().length} / 50 chars min
              </div>
            </label>
            <label className="block text-sm">
              <span className="block mb-1 text-muted-foreground">Reason (optional)</span>
              <input
                type="text" value={reasonInput}
                onChange={(e) => setReasonInput(e.target.value)}
                placeholder="Why this is an ad"
                className="w-full px-3 py-1.5 rounded border border-border bg-background text-foreground text-sm"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={scopeInput === 'global'}
                onChange={(e) => setScopeInput(e.target.checked ? 'global' : 'podcast')} />
              <span>Apply across all podcasts (global pattern)</span>
            </label>
          </div>
        ) : showSponsorPrompt ? (
          <div className="px-6 py-4 border-t border-border bg-secondary/30">
            <label htmlFor="sponsor" className="block text-sm font-medium text-foreground mb-1">
              Sponsor name
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                (so this confirmation can train Stage 2 - leave blank to skip pattern creation)
              </span>
            </label>
            <input
              id="sponsor" type="text" value={sponsorInput}
              onChange={(e) => setSponsorInput(e.target.value)}
              placeholder="e.g. BetterHelp, Squarespace, Progressive"
              className="w-full px-3 py-1.5 rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring text-sm"
            />
          </div>
        ) : (
          <div className="px-6 py-2 border-t border-border text-xs text-muted-foreground">
            Sponsor: <span className="text-foreground">{item.sponsor}</span>{' '}
            <button type="button" onClick={() => setShowSponsorPrompt(true)}
              className="ml-2 underline transition-colors hover:text-foreground">edit</button>
          </div>
        )}

        {/* Action bar */}
        <div className="px-6 py-4 border-t border-border bg-secondary/40 flex items-center justify-between gap-3 flex-wrap">
          {mode === 'create' ? (
            <>
              <div className="text-xs text-muted-foreground">
                Save creates a new ad pattern tagged as `created_by=user`.
              </div>
              <div className="flex items-center gap-2">
                <button type="button" onClick={onClose} disabled={isBusy}
                  className={`px-4 py-1.5 rounded-lg ${ghostBtn} text-sm`}>
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={
                    isBusy ||
                    !sponsorInput.trim() ||
                    textTemplateInput.trim().length < 50 ||
                    !(adStart >= 0 && adEnd > adStart)
                  }
                  onClick={() => {
                    if (!onCreate) return;
                    onCreate({
                      kind: 'create',
                      start: adStart,
                      end: adEnd,
                      sponsor: sponsorInput.trim(),
                      textTemplate: textTemplateInput.trim(),
                      scope: scopeInput,
                      reason: reasonInput,
                    });
                  }}
                  className={`px-4 py-1.5 rounded-lg ${primaryBtn} text-sm`}>
                  {isBusy ? 'Saving...' : 'Save'}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="text-xs text-muted-foreground">
                {boundariesMoved
                  ? 'Confirm will save adjusted boundaries.'
                  : 'Confirm will record this ad as-detected.'}
              </div>
              {/* Equal-width buttons. flex-1 plus basis-0 forces each one
                  to claim the same horizontal slot regardless of label
                  length, so "Save & next" no longer towers over its
                  siblings on narrow viewports. h-9 locks vertical size. */}
              <div className="flex items-stretch gap-2 w-full sm:w-auto">
                <button type="button" onClick={onSkip} disabled={isBusy}
                  className={`flex-1 sm:flex-none sm:min-w-[7rem] basis-0 h-9 px-4 rounded-lg ${ghostBtn} text-sm text-center whitespace-nowrap`}
                  title={hasNext ? 'Skip and advance to the next ad (S)' : 'Skip (S)'}>
                  {hasNext ? 'Skip & Next' : 'Skip'}
                </button>
                <button type="button" onClick={handleReject} disabled={isBusy}
                  className={`flex-1 sm:flex-none sm:min-w-[7rem] basis-0 h-9 px-4 rounded-lg ${destructiveBtn} text-sm text-center whitespace-nowrap`}
                  title="Mark as not an ad (R)">
                  {isBusy ? 'Saving...' : (hasNext ? 'Reject & Next' : 'Reject')}
                </button>
                <button type="button" onClick={handleConfirm} disabled={isBusy}
                  className={`flex-1 sm:flex-none sm:min-w-[7rem] basis-0 h-9 px-4 rounded-lg ${primaryBtn} text-sm text-center whitespace-nowrap`}
                  title="Save changes (C)">
                  {isBusy
                    ? 'Saving...'
                    : hasNext ? 'Save & Next' : 'Save'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default AdReviewModal;
