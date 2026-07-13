import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import WaveSurfer from 'wavesurfer.js';
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js';
import { getTranscriptSpan } from '../api/feeds';
import { getSponsors } from '../api/sponsors';
import { SponsorInput, type SponsorOption } from './ad-editor/SponsorInput';
import { Pin } from './ad-editor/Pin';
import { usePeaks } from './ad-editor/usePeaks';
import { usePeakSlice } from './ad-editor/usePeakSlice';
import { useWaveformWindow } from './ad-editor/useWaveformWindow';
import TextSelectionPanel from './ad-editor/TextSelectionPanel';
import TransportBar from './ad-editor/TransportBar';
import ZoomControl from './ad-editor/ZoomControl';
import { ghostBtn, primaryBtn } from './ad-editor/controlStyles';
import {
  parseTimeInput,
  formatTime,
  getThemeWaveformColors,
  loadPlayWhileDragging,
  savePlayWhileDragging,
} from '../utils/adReviewHelpers';

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

// Cap the default visible window. Some heuristic detections (notably
// post-roll) flag dozens of minutes as a single "ad", which would make
// the default fit-zoom view useless (whole episode squeezed into one
// screen). Six minutes is enough to set ad start with context; user can
// always expand via the +1m buttons or wheel-zoom in.
const DEFAULT_MAX_WINDOW_SECONDS = 360;
const WINDOW_STEP_SECONDS = 60;
// Padding on each side of a detected ad's pins when the modal opens in
// review mode. Small enough to show boundary detail; user can expand via
// wheel-zoom, the +1m buttons, or by typing far-away timestamps.
const CONTEXT_SECONDS = 30;
const MIN_AD_DURATION = 1.0;

// ----------------------------------------------------------------------
// Playhead cursor -- ref-driven DOM updates from the RAF loop, NOT React
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
  const cursorRef = useRef<HTMLDivElement>(null);      // playhead, position-updated from RAF
  const scrubberRef = useRef<HTMLDivElement>(null);    // full-episode play scrubber (seeks audio)
  const windowScrubberRef = useRef<HTMLDivElement>(null); // full-episode pan scrubber (pans zoomed window)
  const audioRef = useRef<HTMLAudioElement>(null);
  // The timeupdate listener that stops "Play selection" at the ad END. Held in
  // a ref so togglePlay/stopPlayback can cancel it, otherwise a stale listener
  // would pause unrelated playback the next time the playhead crosses adEnd.
  const selectionStopRef = useRef<(() => void) | null>(null);
  const wsRef = useRef<WaveSurfer | null>(null);
  const regionsRef = useRef<ReturnType<typeof RegionsPlugin.create> | null>(null);
  const adRegionRef = useRef<ReturnType<RegionsPlugin['addRegion']> | null>(null);

  // Defaults derived from the original detection -- used by Reset.
  // Create mode defaults to the entire episode so the user can pin any
  // moment without zooming/scrolling. Review mode centers on the detected
  // ad with a small context margin so boundary detail is visible at fit
  // zoom; the user can still zoom out or expand by typing distant times.
  const defaults = useMemo(() => {
    const fullDuration = Math.max(0, episodeDuration ?? 0);
    const safeEnd = fullDuration > 0 ? fullDuration : DEFAULT_MAX_WINDOW_SECONDS;
    if (mode === 'create') {
      return {
        windowStart: 0,
        windowEnd: safeEnd,
        adStart: 0,
        adEnd: Math.min(60, safeEnd),
      };
    }
    const windowStart = Math.max(0, item.start - CONTEXT_SECONDS);
    const naturalEnd = item.end + CONTEXT_SECONDS;
    const cappedEnd = windowStart + DEFAULT_MAX_WINDOW_SECONDS;
    return {
      windowStart,
      windowEnd: Math.min(naturalEnd, cappedEnd, safeEnd),
      adStart: (item.correctedBounds ?? item).start,
      adEnd: (item.correctedBounds ?? item).end,
    };
  }, [mode, episodeDuration, item]);

  // Whole-episode duration the windowed waveform spans. Falls back to the
  // default cap when the episode length is unknown so the window math has a
  // finite total to slice against.
  const totalDuration = useMemo(() => {
    const d = Math.max(0, episodeDuration ?? 0);
    return d > 0 ? d : DEFAULT_MAX_WINDOW_SECONDS;
  }, [episodeDuration]);

  const ZOOM_MIN = 1;
  // Review mode opens zoomed onto the ad and the user can zoom deep into
  // boundary detail on long episodes, so allow the same deep zoom the cue
  // editor uses.
  const ZOOM_MAX = 500;

  // Derive the opening view from the detection-derived defaults: center on
  // the default window and pick a zoom that frames it. Create mode's default
  // window is the whole episode -> ~1x; review mode is a tight window -> zoomed.
  const initialCenter = (defaults.windowStart + defaults.windowEnd) / 2;
  const initialZoom = Math.max(
    1,
    totalDuration / Math.max(0.001, defaults.windowEnd - defaults.windowStart),
  );

  // Playhead time mirrored into a ref so a zoom can recenter on the live
  // cursor without re-running the RAF loop. Seeded at the initial center so a
  // zoom BEFORE playback (currentTime still 0) recenters on the ad, not t=0.
  const playheadRef = useRef(initialCenter);
  const {
    zoom, setZoom, zoomIn, zoomOut, windowStart, windowEnd, windowCenter,
    setWindowCenter, reset: resetWindow,
  } = useWaveformWindow(
    totalDuration, initialCenter, playheadRef, ZOOM_MIN, ZOOM_MAX, initialZoom,
  );

  const [adStart, setAdStart] = useState(defaults.adStart);
  const [adEnd, setAdEnd] = useState(defaults.adEnd);
  // String mirror of the timestamp inputs. Lets the user type partial
  // values (`0:3`) without each keystroke being clobbered by a parent
  // numeric update. Commits to adStart/adEnd on blur or Enter.
  const [startInput, setStartInput] = useState(() => formatTime(defaults.adStart));
  const [endInput, setEndInput] = useState(() => formatTime(defaults.adEnd));

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [playbackRate, setPlaybackRate] = useState(1);
  // Bumped by resetView to force a clean re-fetch of peaks. Belt-and-suspenders
  // so Reset always lands a known-good state.
  const [resetTick, setResetTick] = useState(0);
  // Fetch the whole episode's peaks ONCE and slice the visible window out
  // client-side (windowPeaks below). Re-fetching per window would null `peaks`
  // on every zoom/pan tick, flashing the pins and waveform.
  const { peaks, peakResolutionMs, peaksError } = usePeaks(
    item.podcastSlug,
    item.episodeId,
    0,
    totalDuration,
    resetTick,
  );
  const [playWhileDrag, setPlayWhileDrag] = useState<boolean>(loadPlayWhileDragging);
  const wasPlayingBeforeDragRef = useRef(false);
  // Save the playhead position before a pin drag (with playWhileDrag) so
  // we can put it back where the user was listening, instead of stranding
  // it at the new pin position.
  const positionBeforePinDragRef = useRef<number | null>(null);
  const [sponsorInput, setSponsorInput] = useState(item.sponsor ?? '');
  const [showSponsorPrompt, setShowSponsorPrompt] = useState(!item.sponsor);
  // Sponsor catalog, fetched once on mount, used by the SponsorInput
  // combobox in create mode.
  const [sponsorOptions, setSponsorOptions] = useState<SponsorOption[]>([]);
  useEffect(() => {
    let cancelled = false;
    getSponsors()
      .then((list) => {
        if (cancelled) return;
        setSponsorOptions(
          list.map((s: { id: number; name: string }) => ({ id: s.id, name: s.name }))
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  // Create-mode only: a text template the user can edit before submit.
  // Left empty here so the host can wire a transcript-span fetch into it.
  const [textTemplateInput, setTextTemplateInput] = useState('');
  const [scopeInput, setScopeInput] = useState<'podcast' | 'global'>('podcast');
  const [reasonInput, setReasonInput] = useState('');
  // 'audio' uses the waveform + pins, 'text' uses transcript selection.
  // adStart/adEnd are shared across modes so toggling preserves work.
  const [inputMode, setInputMode] = useState<'audio' | 'text'>('audio');
  const textModeActive = mode === 'create' && inputMode === 'text';
  // True once the user has committed a text-mode selection; suppresses the
  // audio-mode transcript-span fetch from clobbering the user's chosen text
  // when they toggle back to audio for fine-tuning.
  const textTemplateFromSelectionRef = useRef(false);

  // Create mode is always against original audio (you can't mark a new ad
  // on already-cut audio). Review mode honors the parent's audioMode.
  const effectiveAudioMode = mode === 'create' ? 'original' : audioMode;
  const audioUrl =
    effectiveAudioMode === 'original' || !processedAudioUrl
      ? `/api/v1/feeds/${item.podcastSlug}/episodes/${item.episodeId}/original.mp3`
      : processedAudioUrl;
  const windowDuration = Math.max(0.001, windowEnd - windowStart);

  // Peaks for just the visible window (a slice of the full-episode peaks).
  const windowPeaks = usePeakSlice(peaks, peakResolutionMs, windowStart, windowEnd);

  // ------------------------------------------------------------------
  // Create mode only: auto-populate text template from the transcript
  // span the user has selected. Debounced; only fills when empty so we
  // don't clobber edits.
  useEffect(() => {
    if (mode !== 'create') return;
    if (inputMode === 'text') return;
    // If the template already comes from a text-mode selection, leave it; the
    // text the user picked is what they want, regardless of where the pins land.
    if (textTemplateFromSelectionRef.current) return;
    if (!(adStart >= 0 && adEnd > adStart)) return;
    const t = setTimeout(() => {
      getTranscriptSpan(item.podcastSlug, item.episodeId, adStart, adEnd)
        .then((res) => {
          setTextTemplateInput(res.text);
        })
        .catch(() => {});
    }, 250);
    return () => clearTimeout(t);
  }, [mode, inputMode, item.podcastSlug, item.episodeId, adStart, adEnd]);

  // ------------------------------------------------------------------
  // Mount wavesurfer when peaks/window arrive. Region is decorative -- 
  // drag/resize disabled because the Pin components own that interaction.
  useEffect(() => {
    if (!containerRef.current || !windowPeaks) return;

    wsRef.current?.destroy();
    wsRef.current = null;

    const regions = RegionsPlugin.create();
    const ws = WaveSurfer.create({
      container: containerRef.current,
      peaks: [windowPeaks],
      duration: windowDuration,
      ...getThemeWaveformColors(),
      cursorColor: 'transparent', // we render our own playhead -- see <Cursor /> below
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      height: 120,
      interact: true,
      plugins: [regions],
    });

    regionsRef.current = regions;
    wsRef.current = ws;

    const region = regions.addRegion({
      start: Math.max(0, adStart - windowStart),
      end: Math.min(windowDuration, adEnd - windowStart),
      color: 'rgba(245, 158, 11, 0.18)',
      drag: false,
      resize: false,
    });
    adRegionRef.current = region;

    // Stop the region from swallowing pointer events -- clicks anywhere in
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
  }, [windowPeaks, windowStart, windowDuration]);

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

  // Apply playback speed to the <audio> element whenever it changes.
  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = playbackRate;
  }, [playbackRate]);

  // ------------------------------------------------------------------
  // Cursor sync: <audio> drives the cursor position via direct DOM update
  // (ref-based, no React re-render). React state is only updated ~10×/s
  // for the transport time readout -- full-rate state updates would
  // re-render the whole modal at 60fps and stutter the cursor.
  useEffect(() => {
    let raf = 0;
    let lastReportedRoundedTime = -1;
    const loop = () => {
      const audio = audioRef.current;
      const cursor = cursorRef.current;
      if (audio && cursor) {
        const t = audio.currentTime;
        playheadRef.current = t;
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
  // Play -- for a post-roll ad whose window is at e.g. 6980-7200s, the
  // cursor would never enter the visible window. Snap to ad-start so the
  // user lands on the ad. We seed to (adStart - 2) for a tiny pre-roll.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const seek = () => {
      audio.currentTime = Math.max(0, adStart - 2);
    };
    if (audio.readyState >= 1 /* HAVE_METADATA */) {
      seek();
    } else {
      audio.addEventListener('loadedmetadata', seek, { once: true });
      return () => audio.removeEventListener('loadedmetadata', seek);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.podcastSlug, item.episodeId, item.start, item.end, resetTick, audioUrl]);

  // ------------------------------------------------------------------
  // Audio playback.
  const clearSelectionStop = () => {
    if (selectionStopRef.current) selectionStopRef.current();
    selectionStopRef.current = null;
  };

  // Play only the ad span (adStart -> adEnd), then pause at the end. Mirrors the
  // cue modal so both editors audition their selection the same way. Seeks after
  // metadata loads (a pre-load seek is dropped by Chrome/Safari). The audition
  // ends on the first user seek after ours -- cursor scrub, pin drag, keyboard,
  // reset -- so the stop listener can never fire on unrelated later playback
  // (the many seek paths do not funnel through one handler to clear it). Like
  // the cue modal, the stop point is the adEnd at play time; editing bounds
  // mid-audition without seeking still stops at the old end.
  const playSelection = () => {
    const audio = audioRef.current;
    if (!audio) return;
    clearSelectionStop();
    const begin = () => {
      const stop = () => {
        const a = audioRef.current;
        if (a && a.currentTime >= adEnd) {
          a.pause();
          setIsPlaying(false);
          clearSelectionStop();
        }
      };
      // Our own seek to adStart below fires one 'seeked' (browsers fire it even
      // when already at that position); skip exactly that one. Any later seek is
      // the user taking over, so cancel the audition.
      let skipInitialSeek = true;
      const onSeeked = () => {
        if (skipInitialSeek) { skipInitialSeek = false; return; }
        clearSelectionStop();
      };
      audio.addEventListener('timeupdate', stop);
      audio.addEventListener('seeked', onSeeked);
      selectionStopRef.current = () => {
        audio.removeEventListener('timeupdate', stop);
        audio.removeEventListener('seeked', onSeeked);
      };
      audio.currentTime = adStart;
      audio.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
    };
    if (audio.readyState >= 1) {
      begin();
    } else {
      audio.addEventListener('loadedmetadata', begin, { once: true });
      selectionStopRef.current = () => audio.removeEventListener('loadedmetadata', begin);
    }
  };

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    clearSelectionStop();
    if (audio.paused) {
      // Safety net: if the cursor is parked at episode origin (or otherwise
      // far before the visible window), snap to the ad start before playing
      // so the user doesn't hear the episode intro on the first Play. A
      // deliberate scrub inside or near the window is preserved.
      if (mode === 'review' && adStart > 1 && audio.currentTime < adStart - 5) {
        audio.currentTime = Math.max(0, adStart - 2);
      }
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
    clearSelectionStop();
    audio.pause();
    audio.currentTime = adStart;
    setIsPlaying(false);
  };

  // Full-episode scrubber: click/drag to seek anywhere in the audio,
  // independent of the waveform window. Uses pointer events so a single
  // handler covers mouse, touch, and stylus.
  const onScrubberPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = scrubberRef.current;
    const audio = audioRef.current;
    const dur = episodeDuration ?? audio?.duration ?? 0;
    if (!el || !audio || !dur) return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const seekFrom = (clientX: number) => {
      const rect = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      audio.currentTime = ratio * dur;
    };
    seekFrom(e.clientX);
    // rAF-throttle moves: pointermove fires at input-device rate (often
    // >100Hz on trackpads). Without this we'd issue a seek per event,
    // which on remote/HLS audio stalls the media element.
    let pendingX: number | null = null;
    let raf = 0;
    const flush = () => {
      raf = 0;
      if (pendingX !== null) {
        seekFrom(pendingX);
        pendingX = null;
      }
    };
    const onMove = (ev: PointerEvent) => {
      pendingX = ev.clientX;
      if (!raf) raf = requestAnimationFrame(flush);
    };
    const onUp = () => {
      if (raf) cancelAnimationFrame(raf);
      el.removeEventListener('pointermove', onMove);
      el.removeEventListener('pointerup', onUp);
      el.removeEventListener('pointercancel', onUp);
    };
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
  };

  // Full-episode pan scrubber: drag to pan the zoomed waveform window across
  // the episode (mirrors CueMarkModal). Does NOT seek audio -- only moves the
  // rendered window so the user can navigate while zoomed in.
  const panToClientX = (clientX: number) => {
    const el = windowScrubberRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    setWindowCenter(frac * totalDuration);
  };
  const onWindowScrubberPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
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
  // Window pan / reset. In the window-width model the rendered view IS the
  // window, so the `,`/`.` keys pan windowCenter rather than growing a fetch
  // span. Span size is changed by zoom.
  const panBack = () => setWindowCenter((c) => c - WINDOW_STEP_SECONDS);
  const panForward = () => setWindowCenter((c) => c + WINDOW_STEP_SECONDS);
  const resetView = () => {
    resetWindow(initialCenter);
    // Anchor the zoom on initialCenter, not the playhead, so Reset lands on the
    // detected ad rather than wherever the user last paused.
    setZoom(initialZoom, initialCenter);
    setAdStart(defaults.adStart);
    setAdEnd(defaults.adEnd);
    // usePeaks clears + re-fetches on resetTick change; the bump is
    // sufficient to force a fresh fetch + waveform rebuild.
    setResetTick((n) => n + 1);
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = defaults.adStart;
      setIsPlaying(false);
    }
  };

  // Mouse-wheel zoom on the waveform, anchored on the time under the cursor.
  // The rendered view IS the window, so there's no horizontal scroll to
  // re-anchor -- setZoom recenters the window on the cursor time directly.
  const onWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    // Only act on vertical wheel (deltaY); leave horizontal wheel alone.
    if (Math.abs(e.deltaY) < Math.abs(e.deltaX)) return;
    e.preventDefault();
    const overlay = overlayRef.current;
    if (!overlay) return;
    const rect = overlay.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const cursorTime = windowStart + frac * windowDuration;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    setZoom(+(zoom * factor).toFixed(3), cursorTime);
  };

  // ------------------------------------------------------------------
  // Submission -- the host owns the actual API call (so it can also
  // refresh the surrounding episode view, navigate, etc.); we just emit.
  const [isBusy, setIsBusy] = useState(false);

  // Mirror adStart/adEnd into the input strings whenever the boundaries
  // change from a source OTHER than user typing (pin drag, reset,
  // keyboard nudge). Skip when an input is focused so we don't fight
  // the user's keystrokes.
  const startInputRef = useRef<HTMLInputElement | null>(null);
  const endInputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (document.activeElement !== startInputRef.current) {
      setStartInput(formatTime(adStart));
    }
  }, [adStart]);
  useEffect(() => {
    if (document.activeElement !== endInputRef.current) {
      setEndInput(formatTime(adEnd));
    }
  }, [adEnd]);

  const commitStartInput = () => {
    const parsed = parseTimeInput(startInput);
    if (parsed === null) {
      setStartInput(formatTime(adStart));
      return;
    }
    // Clamp only to absolute episode bounds. Cross-field validation (Start <
    // End) is enforced at Save time so the user can edit both fields without
    // each blur stomping the other.
    const maxAllowed = episodeDuration ?? Number.POSITIVE_INFINITY;
    const clamped = Math.max(0, Math.min(parsed, maxAllowed));
    setAdStart(clamped);
    setStartInput(formatTime(clamped));
    // Recenter so the just-committed pin stays visible when zoomed.
    setWindowCenter(clamped);
  };
  const commitEndInput = () => {
    const parsed = parseTimeInput(endInput);
    if (parsed === null) {
      setEndInput(formatTime(adEnd));
      return;
    }
    const maxAllowed = episodeDuration ?? Number.POSITIVE_INFINITY;
    const clamped = Math.max(0, Math.min(parsed, maxAllowed));
    setAdEnd(clamped);
    setEndInput(formatTime(clamped));
    // Recenter so the just-committed pin stays visible when zoomed.
    setWindowCenter(clamped);
  };

  const boundariesMoved =
    Math.abs(adStart - item.start) > 0.05 || Math.abs(adEnd - item.end) > 0.05;

  // Plain const: three primitive comparisons are cheaper than useMemo's
  // dep-tracking overhead, and the string|null result has no referential
  // identity worth preserving for downstream memo consumers.
  const boundaryError: string | null =
    adStart < 0
      ? 'Start must be at least 0:00'
      : adEnd <= adStart
        ? 'Start must be before End'
        : adEnd - adStart < MIN_AD_DURATION
          ? `Selection must be at least ${MIN_AD_DURATION}s long`
          : null;
  const inputBorderClass = boundaryError ? 'border-rose-500' : 'border-border';

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
      if (e.key === ',')      { e.preventDefault(); panBack(); return; }
      if (e.key === '.')      { e.preventDefault(); panForward(); return; }
      if (e.key === 'c' || e.key === 'C') { e.preventDefault(); if (!isBusy && !boundaryError) handleConfirm(); return; }
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
  // Style helpers -- explicit hover treatments so buttons clearly
  // highlight on mouseover instead of looking washed out.

  // primaryBtn / ghostBtn come from the shared controlStyles so the transport,
  // zoom, and action buttons all render from one source. destructiveBtn (Not an ad)
  // is unique to this modal.
  const destructiveBtn =
    'bg-destructive text-destructive-foreground transition-all ' +
    'hover:bg-destructive hover:ring-2 hover:ring-destructive hover:ring-offset-2 hover:ring-offset-card ' +
    'disabled:opacity-50 disabled:cursor-not-allowed';

  // ------------------------------------------------------------------

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 backdrop-blur-sm p-4"
      onMouseDown={(e) => {
        // Only close when the bare backdrop is the actual mousedown target.
        // Anything inside the modal panel (inputs, buttons, listbox items
        // from a child popup, etc.) gets ignored here without needing a
        // child stopPropagation. In create mode we never auto-close on
        // backdrop click -- the user is in the middle of data entry and
        // an accidental tap would lose everything.
        if (e.target !== e.currentTarget) return;
        if (mode === 'create') return;
        onClose();
      }}
    >
      <div
        className="bg-card rounded-lg border border-border w-full max-w-4xl max-h-[90vh] overflow-y-auto shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header -- action chrome up top (always fits a single row on
            mobile), title visible only at sm:+ where there's room, and
            the detection metadata wraps onto its own line below. */}
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-border space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="hidden sm:block text-lg font-semibold text-foreground truncate min-w-0">
              {mode === 'create' ? 'Add new ad' : 'Detected ad'}
            </h2>
            <div className="flex items-center gap-1.5 sm:gap-2 ml-auto">
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
              {/* + Add new ad. Icon-only on mobile, full label on sm:+. */}
              {mode === 'review' && onAddNew && (
                <button
                  type="button"
                  onClick={onAddNew}
                  aria-label="Add new ad"
                  title="Add new ad"
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                  </svg>
                  <span className="hidden sm:inline">Add new ad</span>
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
          {/* Detection metadata sits on its own row below the action chrome
              so it can't push the toggle/close into a wrap on narrow screens. */}
          {mode === 'review' && (
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>Stage: {item.detectionStage ?? '-'}</span>
              {item.confidence !== null && <span>Confidence: {Math.round(item.confidence * 100)}%</span>}
              {item.patternId !== null && <span>Pattern #{item.patternId}</span>}
              {item.reason && <span className="italic truncate max-w-full" title={item.reason}>{item.reason}</span>}
            </div>
          )}
        </div>

        {/* Window controls + reset */}
        {/* Window header strip. ±1m buttons removed per the 2.2.0 plan;
            the keyboard (`,` / `.`) handler still expands/shrinks the
            window, and pin drag controls the ad boundaries themselves.
            Window time labels prefixed so they're not a bare pair of
            numbers floating in the chrome. */}
        <div className={`px-4 sm:px-6 pt-3 sm:pt-4 flex items-center justify-between gap-3 flex-wrap text-xs text-muted-foreground tabular-nums ${textModeActive ? 'hidden' : ''}`}>
          <span>
            Window: {formatTime(windowStart)} – {formatTime(windowEnd)}
          </span>
          <div className="flex items-center gap-3 flex-wrap">
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
        </div>

        {/* Full-episode pan scrubber: shows where the zoomed window sits and
            lets the user pan across the whole episode (only useful when
            zoomed). Pans the rendered window -- it does NOT seek audio. */}
        {!textModeActive && zoom > 1 && totalDuration > 0 && (
          <div className="px-4 sm:px-6 pt-2">
            <div
              ref={windowScrubberRef}
              role="slider"
              aria-label="Waveform window position"
              aria-valuemin={0}
              aria-valuemax={Math.round(totalDuration)}
              aria-valuenow={Math.round(windowCenter)}
              tabIndex={0}
              onPointerDown={onWindowScrubberPointerDown}
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
                style={{ left: `${(currentTime / totalDuration) * 100}%` }}
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

        {mode === 'create' && (
          <div className="px-4 sm:px-6 pt-3">
            <div className="inline-flex rounded-md border border-input overflow-hidden" role="group">
              <button
                type="button"
                onClick={() => setInputMode('audio')}
                className={`px-3 py-1.5 text-xs transition-colors ${
                  inputMode === 'audio'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-background text-muted-foreground hover:bg-secondary'
                }`}
                title="Mark the ad on the waveform"
              >
                By audio
              </button>
              <button
                type="button"
                onClick={() => setInputMode('text')}
                className={`px-3 py-1.5 text-xs transition-colors ${
                  inputMode === 'text'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-background text-muted-foreground hover:bg-secondary'
                }`}
                title="Mark the ad by selecting transcript text"
              >
                By text
              </button>
            </div>
          </div>
        )}

        {textModeActive && (
          <TextSelectionPanel
            slug={item.podcastSlug}
            episodeId={item.episodeId}
            episodeDuration={episodeDuration}
            audioRef={audioRef}
            adStart={adStart}
            adEnd={adEnd}
            onSelectionChange={(start, end, text) => {
              setAdStart(start);
              setAdEnd(end);
              setTextTemplateInput(text);
              textTemplateFromSelectionRef.current = true;
            }}
            playbackRate={playbackRate}
            setPlaybackRate={setPlaybackRate}
          />
        )}

        {/* Waveform + pin overlay. Hidden when text mode is active. */}
        <div className={`px-6 py-4 ${textModeActive ? 'hidden' : ''}`}>
          <div className="bg-secondary/40 rounded-lg p-3 min-h-[180px]">
            {peaksError ? (
              <p className="text-sm text-destructive">Failed to load waveform: {peaksError}</p>
            ) : !peaks ? (
              <p className="text-sm text-muted-foreground">Loading waveform…</p>
            ) : (
              <div
                onWheel={onWheel}
                className="overflow-hidden"
              >
                <div className="relative w-full" ref={overlayRef}>
                  {/* Header strip -- gives the pinheads a place to live INSIDE
                      the overlay's box (so they aren't clipped). */}
                  <div className="h-9" />
                  {/* Pins live in the same horizontal coordinate system as the
                      waveform host (overlayRef), which is always exactly the
                      visible window width, so pin `left: %` of windowDuration
                      tracks the right time at any zoom. */}
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
                      // boundary -- user can listen anywhere in context.
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
                    {/* Time label -- hover or while moving. */}
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

          {/* Zoom -- shared with the "Mark cue" editor. */}
          <ZoomControl
            value={zoom}
            min={ZOOM_MIN}
            max={ZOOM_MAX}
            step={0.1}
            onChange={(z) => setZoom(z)}
            onZoomIn={zoomIn}
            onZoomOut={zoomOut}
          />

          {/* Full-episode scrubber: dim band = visible waveform window,
              bright fill = playback progress. */}
          {(() => {
            const epDur = episodeDuration ?? 0;
            const pct = (t: number) => (epDur > 0 ? (t / epDur) * 100 : 0);
            const onScrubberKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
              const audio = audioRef.current;
              if (!audio || !epDur) return;
              const step = e.shiftKey ? 10 : 5;
              let next: number;
              if (e.key === 'ArrowLeft') next = Math.max(0, audio.currentTime - step);
              else if (e.key === 'ArrowRight') next = Math.min(epDur, audio.currentTime + step);
              else if (e.key === 'Home') next = 0;
              else if (e.key === 'End') next = epDur;
              else return;
              e.preventDefault();
              audio.currentTime = next;
            };
            return (
              <div className="mt-2 flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
                <span className="w-12 text-right shrink-0">{formatTime(currentTime)}</span>
                <div
                  ref={scrubberRef}
                  role="slider"
                  aria-label="Episode progress"
                  aria-valuemin={0}
                  aria-valuemax={epDur}
                  aria-valuenow={currentTime}
                  tabIndex={0}
                  onPointerDown={onScrubberPointerDown}
                  onKeyDown={onScrubberKeyDown}
                  className="group relative flex-1 h-3 rounded-full bg-background border border-border cursor-pointer touch-none focus:outline-hidden focus:ring-2 focus:ring-ring"
                >
                  {episodeDuration ? (
                    <>
                      <div
                        aria-hidden="true"
                        className="absolute inset-y-0 bg-muted-foreground/25 pointer-events-none"
                        style={{
                          left: `${pct(windowStart)}%`,
                          width: `${pct(windowEnd - windowStart)}%`,
                        }}
                      />
                      <div
                        aria-hidden="true"
                        className="absolute inset-y-0 left-0 rounded-l-full bg-primary pointer-events-none"
                        style={{ width: `${pct(currentTime)}%` }}
                      />
                      <div
                        aria-hidden="true"
                        className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full bg-primary ring-2 ring-background shadow-sm pointer-events-none group-hover:scale-110 group-focus:scale-110 transition-transform"
                        style={{ left: `${pct(currentTime)}%` }}
                      />
                    </>
                  ) : null}
                </div>
                <span className="w-12 text-left shrink-0">{formatTime(epDur)}</span>
              </div>
            );
          })()}

          {/* Boundaries readout. The two timestamps are editable inputs
              (M:SS, MM:SS, H:MM:SS, or raw seconds) that commit to the
              ad boundaries on blur or Enter. The pin handles still
              update them live during drag. */}
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground tabular-nums">
            <span>Selection:</span>
            <input
              ref={startInputRef}
              type="text"
              inputMode="decimal"
              value={startInput}
              aria-label="Selection start time"
              aria-invalid={boundaryError !== null}
              onChange={(e) => setStartInput(e.target.value)}
              onBlur={commitStartInput}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  (e.target as HTMLInputElement).blur();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  setStartInput(formatTime(adStart));
                  (e.target as HTMLInputElement).blur();
                }
              }}
              className={`w-20 px-1.5 py-0.5 rounded border bg-background text-emerald-500 font-medium text-center tabular-nums focus:outline-hidden focus:ring-2 focus:ring-ring ${inputBorderClass}`}
            />
            <span>-</span>
            <input
              ref={endInputRef}
              type="text"
              inputMode="decimal"
              value={endInput}
              aria-label="Selection end time"
              aria-invalid={boundaryError !== null}
              onChange={(e) => setEndInput(e.target.value)}
              onBlur={commitEndInput}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  (e.target as HTMLInputElement).blur();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  setEndInput(formatTime(adEnd));
                  (e.target as HTMLInputElement).blur();
                }
              }}
              className={`w-20 px-1.5 py-0.5 rounded border bg-background text-rose-500 font-medium text-center tabular-nums focus:outline-hidden focus:ring-2 focus:ring-ring ${inputBorderClass}`}
            />
            <span className="text-xs">({Math.round((adEnd - adStart) * 10) / 10}s)</span>
            {boundariesMoved && !boundaryError && (
              <span className="text-xs text-amber-500">
                (originally {formatTime(item.start)} – {formatTime(item.end)})
              </span>
            )}
          </div>
          {boundaryError && (
            <div
              role="alert"
              className="mt-1.5 inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-rose-500/10 text-rose-500 text-xs font-medium"
            >
              <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
              <span>{boundaryError}</span>
            </div>
          )}

          <audio
            ref={audioRef}
            src={audioUrl}
            preload="metadata"
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            onEnded={() => setIsPlaying(false)}
          />

          {/* Playback transport -- shared with the "Mark cue" editor. */}
          <TransportBar
            isPlaying={isPlaying}
            onTogglePlay={togglePlay}
            onSeekToStart={seekToAdStart}
            onSeekToEnd={seekToAdEnd}
            onSeekRelative={seekRelative}
            onStop={stopPlayback}
            playbackRate={playbackRate}
            onPlaybackRateChange={setPlaybackRate}
            currentTime={currentTime}
            selectionDuration={adEnd - adStart}
            inSelection={currentTime >= adStart && currentTime <= adEnd}
            selectionLabel="inside ad"
            onPlaySelection={playSelection}
          />

          <div className="mt-2 text-xs text-muted-foreground">
            Drag the <span className="text-emerald-500 font-semibold">START</span> /{' '}
            <span className="text-rose-500 font-semibold">END</span> pins above the waveform.{' '}
            <kbd>Space</kbd> play • <kbd>,</kbd>/<kbd>.</kbd> expand window • mouse-wheel to zoom • <kbd>C</kbd> confirm • <kbd>R</kbd> not an ad • <kbd>S</kbd> skip
          </div>
        </div>

        {/* Sponsor prompt + (in create mode) text-template + scope */}
        {mode === 'create' ? (
          <div className="px-4 sm:px-6 py-3 sm:py-4 border-t border-border bg-secondary/30 space-y-3">
            <label className="block text-sm font-medium text-foreground">
              Sponsor name
              <div className="mt-1">
                <SponsorInput
                  value={sponsorInput}
                  onChange={setSponsorInput}
                  sponsors={sponsorOptions}
                />
              </div>
            </label>
            <label className="block text-sm font-medium text-foreground">
              Text template
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                (filled from the transcript; edit before save)
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
          <div className="px-4 sm:px-6 py-3 sm:py-4 border-t border-border bg-secondary/30">
            <label htmlFor="sponsor" className="block text-sm font-medium text-foreground mb-1">
              Sponsor name
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                (trains Stage 2; leave blank to skip pattern creation)
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
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-t border-border bg-secondary/40 flex items-center justify-between gap-2 sm:gap-3 flex-wrap">
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
                    boundaryError !== null
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
              {/* Equal-width buttons across viewports. Short labels
                  on mobile (so 3 fit on a 320-360px viewport), full
                  "& Next" labels on sm: where there's room. */}
              <div className="flex items-stretch gap-1.5 sm:gap-2 w-full sm:w-auto">
                <button type="button" onClick={onSkip} disabled={isBusy}
                  className={`flex-1 sm:flex-none sm:min-w-[7rem] basis-0 h-9 px-2 sm:px-4 rounded-lg ${ghostBtn} text-sm text-center whitespace-nowrap`}
                  title={hasNext ? 'Skip and advance to the next ad (S)' : 'Skip (S)'}>
                  <span className="sm:hidden">Skip</span>
                  <span className="hidden sm:inline">{hasNext ? 'Skip & Next' : 'Skip'}</span>
                </button>
                <button type="button" onClick={handleReject} disabled={isBusy}
                  className={`flex-1 sm:flex-none sm:min-w-[7rem] basis-0 h-9 px-2 sm:px-4 rounded-lg ${destructiveBtn} text-sm text-center whitespace-nowrap`}
                  title="Mark as not an ad (R)">
                  {isBusy ? '...' : (<>
                    <span className="sm:hidden">Not an ad</span>
                    <span className="hidden sm:inline">{hasNext ? 'Not an ad & Next' : 'Not an ad'}</span>
                  </>)}
                </button>
                <button type="button" onClick={handleConfirm} disabled={isBusy || boundaryError !== null}
                  className={`flex-1 sm:flex-none sm:min-w-[7rem] basis-0 h-9 px-2 sm:px-4 rounded-lg ${primaryBtn} text-sm text-center whitespace-nowrap`}
                  title={boundaryError ?? "Save changes (C)"}>
                  {isBusy ? '...' : (<>
                    <span className="sm:hidden">Save</span>
                    <span className="hidden sm:inline">{hasNext ? 'Save & Next' : 'Save'}</span>
                  </>)}
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
