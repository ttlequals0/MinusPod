import { useState, useRef, useEffect, useCallback } from 'react';
import { useTranscriptKeyboard } from '../hooks/useTranscriptKeyboard';
import { X, Check, RotateCcw, Save, Play, Pause, ChevronLeft, ChevronRight, ChevronUp, ChevronDown } from 'lucide-react';

interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

// Touch interaction mode for mobile
type TouchMode = 'seek' | 'setStart' | 'setEnd';

// Save status for visual feedback
type SaveStatus = 'idle' | 'saving' | 'success' | 'error';

interface DetectedAd {
  start: number;
  end: number;
  confidence: number;
  reason: string;
  sponsor?: string;
  pattern_id?: number;
  detection_stage?: string;
  scope?: string;
  network_id?: string;
}

interface TranscriptEditorProps {
  segments: TranscriptSegment[];
  detectedAds: DetectedAd[];
  audioUrl?: string;
  onCorrection: (correction: AdCorrection) => void;
  onClose?: () => void;
  initialSeekTime?: number;
  saveStatus?: SaveStatus;
  selectedAdIndex?: number;
  onSelectedAdIndexChange?: (index: number) => void;
}

export interface AdCorrection {
  type: 'confirm' | 'reject' | 'adjust';
  originalAd: DetectedAd;
  adjustedStart?: number;
  adjustedEnd?: number;
  notes?: string;
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function TranscriptEditor({
  segments,
  detectedAds,
  audioUrl,
  onCorrection,
  onClose,
  initialSeekTime,
  saveStatus = 'idle',
  selectedAdIndex: externalSelectedAdIndex,
  onSelectedAdIndexChange,
}: TranscriptEditorProps) {
  // Use controlled state if external index is provided, otherwise use internal state
  const [internalSelectedAdIndex, setInternalSelectedAdIndex] = useState(0);
  const selectedAdIndex = externalSelectedAdIndex ?? internalSelectedAdIndex;

  // Ref to always have current selectedAdIndex for callbacks (avoids stale closures)
  const selectedAdIndexRef = useRef(selectedAdIndex);
  selectedAdIndexRef.current = selectedAdIndex;

  const setSelectedAdIndex = useCallback((index: number) => {
    if (onSelectedAdIndexChange) {
      onSelectedAdIndexChange(index);
    } else {
      setInternalSelectedAdIndex(index);
    }
  }, [onSelectedAdIndexChange]);
  const [adjustedStart, setAdjustedStart] = useState(0);
  const [adjustedEnd, setAdjustedEnd] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [touchMode, setTouchMode] = useState<TouchMode>('seek');
  const [lastTapTime, setLastTapTime] = useState(0);
  const [longPressTimer, setLongPressTimer] = useState<ReturnType<typeof setTimeout> | null>(null);
  const [mobileControlsExpanded, setMobileControlsExpanded] = useState(false);
  const [audioSheetExpanded, setAudioSheetExpanded] = useState(false);
  const [swipeStartX, setSwipeStartX] = useState<number | null>(null);
  const [isDraggingProgress, setIsDraggingProgress] = useState(false);
  const [preserveSeekPosition, setPreserveSeekPosition] = useState(false);
  const [showReason, setShowReason] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const progressBarRef = useRef<HTMLDivElement>(null);

  // Haptic feedback helper
  const triggerHaptic = useCallback((style: 'light' | 'medium' | 'heavy' = 'light') => {
    if ('vibrate' in navigator) {
      navigator.vibrate(style === 'light' ? 10 : style === 'medium' ? 20 : 30);
    }
  }, []);

  const NUDGE_AMOUNT = 0.5; // seconds
  const DOUBLE_TAP_DELAY = 300; // ms
  const LONG_PRESS_DELAY = 500; // ms

  const selectedAd = detectedAds[selectedAdIndex];

  // Initialize adjusted bounds when ad changes
  useEffect(() => {
    if (selectedAd) {
      setAdjustedStart(selectedAd.start);
      setAdjustedEnd(selectedAd.end);
    }
  }, [selectedAd]);

  // Update current time from audio
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handleTimeUpdate = () => setCurrentTime(audio.currentTime);
    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);

    audio.addEventListener('timeupdate', handleTimeUpdate);
    audio.addEventListener('play', handlePlay);
    audio.addEventListener('pause', handlePause);

    return () => {
      audio.removeEventListener('timeupdate', handleTimeUpdate);
      audio.removeEventListener('play', handlePlay);
      audio.removeEventListener('pause', handlePause);
    };
  }, []);

  // Auto-scroll transcript to current time during playback
  useEffect(() => {
    if (!transcriptRef.current || !isPlaying) return;

    const activeSegment = transcriptRef.current.querySelector('[data-active="true"]');
    if (activeSegment) {
      activeSegment.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [currentTime, isPlaying]);

  // Scroll transcript to show selected ad
  const scrollToAd = useCallback((ad: DetectedAd) => {
    if (!transcriptRef.current) return;

    const segmentElements = transcriptRef.current.querySelectorAll('[data-segment-start]');
    for (const seg of segmentElements) {
      const start = parseFloat(seg.getAttribute('data-segment-start') || '0');
      if (start >= ad.start) {
        seg.scrollIntoView({ behavior: 'smooth', block: 'center' });
        break;
      }
    }
  }, []);

  // Scroll transcript to a specific time (for jump functionality)
  const scrollToTime = useCallback((time: number) => {
    if (!transcriptRef.current) return;

    const segmentElements = transcriptRef.current.querySelectorAll('[data-segment-start]');
    for (const seg of segmentElements) {
      const start = parseFloat(seg.getAttribute('data-segment-start') || '0');
      if (start >= time) {
        seg.scrollIntoView({ behavior: 'smooth', block: 'center' });
        break;
      }
    }
  }, []);

  // Handle initial seek time (from Jump button)
  useEffect(() => {
    if (initialSeekTime !== undefined && audioRef.current) {
      // Find the ad that contains this time (with tolerance for floating-point precision)
      const adIndex = detectedAds.findIndex(
        (ad) => Math.abs(initialSeekTime - ad.start) < 0.5 ||
                (initialSeekTime > ad.start && initialSeekTime <= ad.end)
      );
      if (adIndex !== -1) {
        setSelectedAdIndex(adIndex);
      }
      // Seek to the time
      audioRef.current.currentTime = initialSeekTime;
      // Preserve this seek position so play doesn't reset it
      setPreserveSeekPosition(true);
      // Scroll transcript to show the jumped-to time
      scrollToTime(initialSeekTime);
    }
  }, [initialSeekTime, detectedAds, scrollToTime]);

  // Auto-focus container when editor opens for keyboard shortcuts
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.focus();
    }
  }, []);

  // Handle ad selection with auto-scroll and haptic feedback
  const handleAdSelect = useCallback((index: number) => {
    setSelectedAdIndex(index);
    triggerHaptic('light');
    const ad = detectedAds[index];
    if (ad) {
      // Small delay to allow state update before scroll
      setTimeout(() => scrollToAd(ad), 50);
    }
  }, [detectedAds, scrollToAd, triggerHaptic]);

  // Navigate to previous/next ad (for swipe gestures)
  // Uses ref to avoid stale closure with controlled state
  const goToPreviousAd = useCallback(() => {
    const currentIndex = selectedAdIndexRef.current;
    if (currentIndex > 0) {
      handleAdSelect(currentIndex - 1);
    }
  }, [handleAdSelect]);

  const goToNextAd = useCallback(() => {
    const currentIndex = selectedAdIndexRef.current;
    if (currentIndex < detectedAds.length - 1) {
      handleAdSelect(currentIndex + 1);
    }
  }, [detectedAds.length, handleAdSelect]);

  // Swipe gesture handlers for ad navigation
  const handleSwipeStart = useCallback((e: React.TouchEvent) => {
    setSwipeStartX(e.touches[0].clientX);
  }, []);

  const handleSwipeEnd = useCallback((e: React.TouchEvent) => {
    if (swipeStartX === null) return;
    const deltaX = e.changedTouches[0].clientX - swipeStartX;
    const SWIPE_THRESHOLD = 50;
    if (Math.abs(deltaX) > SWIPE_THRESHOLD) {
      if (deltaX > 0) {
        goToPreviousAd();
      } else {
        goToNextAd();
      }
    }
    setSwipeStartX(null);
  }, [swipeStartX, goToPreviousAd, goToNextAd]);

  const handlePlayPause = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
    } else {
      // Only reset to ad start if not preserving a jump position AND outside ad bounds
      if (!preserveSeekPosition && (currentTime < adjustedStart || currentTime > adjustedEnd)) {
        audio.currentTime = adjustedStart;
      }
      // Clear the preserve flag after use
      setPreserveSeekPosition(false);
      audio.play();
    }
  }, [isPlaying, currentTime, adjustedStart, adjustedEnd, preserveSeekPosition]);

  const handleNudgeEndForward = useCallback(() => {
    setAdjustedEnd((prev) => Math.min(prev + NUDGE_AMOUNT, segments[segments.length - 1]?.end || prev));
    triggerHaptic('light');
  }, [segments, triggerHaptic]);

  const handleNudgeEndBackward = useCallback(() => {
    setAdjustedEnd((prev) => Math.max(prev - NUDGE_AMOUNT, adjustedStart + 1));
    triggerHaptic('light');
  }, [adjustedStart, triggerHaptic]);

  const handleNudgeStartForward = useCallback(() => {
    setAdjustedStart((prev) => Math.min(prev + NUDGE_AMOUNT, adjustedEnd - 1));
    triggerHaptic('light');
  }, [adjustedEnd, triggerHaptic]);

  const handleNudgeStartBackward = useCallback(() => {
    setAdjustedStart((prev) => Math.max(prev - NUDGE_AMOUNT, 0));
    triggerHaptic('light');
  }, [triggerHaptic]);

  const handleSave = useCallback(() => {
    if (!selectedAd || saveStatus === 'saving') return;

    triggerHaptic('medium');
    const hasChanges =
      adjustedStart !== selectedAd.start || adjustedEnd !== selectedAd.end;

    onCorrection({
      type: hasChanges ? 'adjust' : 'confirm',
      originalAd: selectedAd,
      adjustedStart: hasChanges ? adjustedStart : undefined,
      adjustedEnd: hasChanges ? adjustedEnd : undefined,
    });

    // Move to next ad
    if (selectedAdIndex < detectedAds.length - 1) {
      setSelectedAdIndex(selectedAdIndex + 1);
    }
  }, [selectedAd, adjustedStart, adjustedEnd, onCorrection, selectedAdIndex, detectedAds.length, saveStatus, triggerHaptic]);

  const handleReset = useCallback(() => {
    if (selectedAd) {
      setAdjustedStart(selectedAd.start);
      setAdjustedEnd(selectedAd.end);
    }
  }, [selectedAd]);

  const handleConfirm = useCallback(() => {
    if (!selectedAd || saveStatus === 'saving') return;
    triggerHaptic('medium');
    onCorrection({
      type: 'confirm',
      originalAd: selectedAd,
    });
    if (selectedAdIndex < detectedAds.length - 1) {
      setSelectedAdIndex(selectedAdIndex + 1);
    }
  }, [selectedAd, onCorrection, selectedAdIndex, detectedAds.length, saveStatus, triggerHaptic]);

  const handleReject = useCallback(() => {
    if (!selectedAd || saveStatus === 'saving') return;
    triggerHaptic('heavy');
    onCorrection({
      type: 'reject',
      originalAd: selectedAd,
    });
    if (selectedAdIndex < detectedAds.length - 1) {
      setSelectedAdIndex(selectedAdIndex + 1);
    }
  }, [selectedAd, onCorrection, selectedAdIndex, detectedAds.length, saveStatus, triggerHaptic]);

  // Set up keyboard shortcuts
  useTranscriptKeyboard({
    onPlayPause: handlePlayPause,
    onNudgeEndForward: handleNudgeEndForward,
    onNudgeEndBackward: handleNudgeEndBackward,
    onNudgeStartForward: handleNudgeStartForward,
    onNudgeStartBackward: handleNudgeStartBackward,
    onSave: handleSave,
    onReset: handleReset,
    onConfirm: handleConfirm,
    onReject: handleReject,
  });

  const seekTo = (time: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = time;
    }
  };

  // Handle segment click with modifier key support
  const handleSegmentClick = useCallback((segment: TranscriptSegment, e: React.MouseEvent) => {
    // Shift+click sets END boundary
    if (e.shiftKey) {
      setAdjustedEnd(segment.end);
      return;
    }
    // Alt/Option+click or Cmd/Ctrl+click sets START boundary
    if (e.altKey || e.metaKey || e.ctrlKey) {
      setAdjustedStart(segment.start);
      return;
    }
    // Normal click seeks audio
    seekTo(segment.start);
  }, []);

  // Handle touch start for long-press detection
  const handleTouchStart = useCallback((segment: TranscriptSegment) => {
    const timer = setTimeout(() => {
      // Long press - set END boundary
      setAdjustedEnd(segment.end);
      setLongPressTimer(null);
    }, LONG_PRESS_DELAY);
    setLongPressTimer(timer);
  }, []);

  // Handle touch end for tap/double-tap detection
  const handleTouchEnd = useCallback((segment: TranscriptSegment, e: React.TouchEvent) => {
    // Cancel long press if it was a short touch
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      setLongPressTimer(null);
    } else {
      // Long press already fired, don't process tap
      return;
    }

    const now = Date.now();
    const timeSinceLastTap = now - lastTapTime;

    if (timeSinceLastTap < DOUBLE_TAP_DELAY) {
      // Double tap - set START boundary
      e.preventDefault();
      setAdjustedStart(segment.start);
      setLastTapTime(0);
    } else {
      // Single tap - handle based on touch mode
      setLastTapTime(now);
      switch (touchMode) {
        case 'setStart':
          setAdjustedStart(segment.start);
          break;
        case 'setEnd':
          setAdjustedEnd(segment.end);
          break;
        case 'seek':
        default:
          seekTo(segment.start);
          break;
      }
    }
  }, [touchMode, lastTapTime, longPressTimer]);

  // Clean up long press timer on touch cancel/move
  const handleTouchCancel = useCallback(() => {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      setLongPressTimer(null);
    }
  }, [longPressTimer]);

  // Handle click on progress bar to seek
  const handleProgressClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const percentage = clickX / rect.width;
    const duration = segments[segments.length - 1]?.end || 1;
    seekTo(percentage * duration);
  };

  // Draggable progress bar handlers
  const handleProgressDragStart = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    setIsDraggingProgress(true);
    triggerHaptic('light');
    const rect = e.currentTarget.getBoundingClientRect();
    const touchX = e.touches[0].clientX - rect.left;
    const percentage = Math.max(0, Math.min(1, touchX / rect.width));
    const duration = segments[segments.length - 1]?.end || 1;
    seekTo(percentage * duration);
  }, [segments, triggerHaptic]);

  const handleProgressDrag = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    if (!isDraggingProgress) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const touchX = e.touches[0].clientX - rect.left;
    const percentage = Math.max(0, Math.min(1, touchX / rect.width));
    const duration = segments[segments.length - 1]?.end || 1;
    seekTo(percentage * duration);
  }, [isDraggingProgress, segments]);

  const handleProgressDragEnd = useCallback(() => {
    setIsDraggingProgress(false);
  }, []);

  const isInAdRegion = (segStart: number, segEnd: number) => {
    return segStart < adjustedEnd && segEnd > adjustedStart;
  };

  const isCurrentSegment = (segStart: number, segEnd: number) => {
    return currentTime >= segStart && currentTime < segEnd;
  };

  // Get button text based on save status
  const getSaveButtonText = () => {
    switch (saveStatus) {
      case 'saving': return 'Saving...';
      case 'success': return 'Saved!';
      case 'error': return 'Error!';
      default: return 'Save Adjusted';
    }
  };

  const getConfirmButtonText = () => {
    switch (saveStatus) {
      case 'saving': return 'Saving...';
      case 'success': return 'Saved!';
      case 'error': return 'Error!';
      default: return 'Confirm';
    }
  };

  const getRejectButtonText = () => {
    switch (saveStatus) {
      case 'saving': return 'Saving...';
      case 'success': return 'Saved!';
      case 'error': return 'Error!';
      default: return 'Not an Ad';
    }
  };

  if (!selectedAd) {
    return (
      <div className="p-4 text-center text-muted-foreground">
        No ads to review
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      className="flex flex-col h-[75vh] sm:h-[70vh] max-h-[600px] sm:max-h-[800px] landscape:h-[90vh] landscape:max-h-none bg-card rounded-lg border border-border outline-none focus:ring-2 focus:ring-primary/50 overflow-hidden"
    >
      {/* STICKY TOP: Header, Ad Selector, Boundary Controls */}
      <div className="sticky top-0 z-20 bg-card flex-shrink-0">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2 flex-wrap">
            {/* Mobile header (no arrows, navigation is in center bar) */}
            <h3 className="text-sm font-medium sm:hidden landscape:hidden">
              Ad {selectedAdIndex + 1} of {detectedAds.length}
            </h3>
            {selectedAd.sponsor && (
              <span className="px-2 py-0.5 text-xs bg-primary/10 text-primary rounded">
                {selectedAd.sponsor}
              </span>
            )}
            {selectedAd.scope && (
              <span className={`px-2 py-0.5 text-xs rounded ${
                selectedAd.scope === 'global'
                  ? 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
                  : selectedAd.scope === 'network'
                  ? 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
                  : 'bg-green-500/20 text-green-600 dark:text-green-400'
              }`}>
                {selectedAd.scope === 'global' ? 'Global' :
                 selectedAd.scope === 'network' ? `Network: ${selectedAd.network_id || '?'}` :
                 'Podcast'}
              </span>
            )}
            {selectedAd.detection_stage && (
              <span className="px-2 py-0.5 text-xs bg-muted text-muted-foreground rounded">
                {selectedAd.detection_stage}
              </span>
            )}
            {selectedAd.reason && (
              <button
                onClick={() => setShowReason(!showReason)}
                className="px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent rounded transition-colors"
              >
                {showReason ? 'Hide reason' : 'Show reason'}
              </button>
            )}
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Close"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
        {/* Collapsible reason section */}
        {showReason && selectedAd.reason && (
          <div className="px-4 py-2 border-b border-border bg-muted/30 text-sm text-muted-foreground">
            <p className="break-words">{selectedAd.reason}</p>
          </div>
        )}

        {/* Ad selector - with momentum scrolling for mobile */}
        <div className="flex gap-2 px-4 py-2 border-b border-border overflow-x-auto scroll-smooth touch-pan-x landscape:hidden">
          {detectedAds.map((ad, index) => (
            <button
              key={index}
              onClick={() => handleAdSelect(index)}
              className={`px-4 py-3 sm:px-3 sm:py-1.5 text-sm sm:text-xs rounded-lg whitespace-nowrap touch-manipulation min-h-[44px] sm:min-h-0 active:scale-95 transition-all ${
                index === selectedAdIndex
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted hover:bg-accent active:bg-accent/80'
              }`}
            >
              {formatTime(ad.start)}
            </button>
          ))}
        </div>
        {/* Center navigation - visible on desktop and landscape */}
        <div className="hidden sm:flex landscape:flex items-center justify-center gap-2 px-4 py-1.5 border-b border-border text-sm">
          <button
            onClick={goToPreviousAd}
            disabled={selectedAdIndex === 0}
            className="p-1.5 rounded hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            aria-label="Previous ad"
            title="Previous ad"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span>Ad {selectedAdIndex + 1} of {detectedAds.length}</span>
          <button
            onClick={goToNextAd}
            disabled={selectedAdIndex >= detectedAds.length - 1}
            className="p-1.5 rounded hover:bg-accent disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            aria-label="Next ad"
            title="Next ad"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>

        {/* Boundary controls - Collapsible on mobile */}
        <div className="border-b border-border bg-muted/30 landscape:hidden">
          {/* Mobile toggle button - shows current bounds, hidden on sm+ */}
          <button
            onClick={() => setMobileControlsExpanded(!mobileControlsExpanded)}
            className="w-full px-4 py-3 flex items-center justify-between sm:hidden touch-manipulation min-h-[48px] active:bg-accent/50 transition-colors"
          >
            <span className="text-sm font-mono text-muted-foreground">
              {formatTime(adjustedStart)} - {formatTime(adjustedEnd)}
            </span>
            <div className="flex items-center gap-1 text-xs text-primary">
              <span>{mobileControlsExpanded ? 'Hide' : 'Adjust'}</span>
              {mobileControlsExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </div>
          </button>

          {/* Controls - always visible on sm+, conditionally on mobile */}
          <div className={`px-4 py-3 ${mobileControlsExpanded ? 'block' : 'hidden'} sm:block`}>
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
              <div className="flex items-center gap-3 sm:gap-2 justify-center sm:justify-start">
                <span className="text-xs text-muted-foreground">Start:</span>
                <button
                  onClick={handleNudgeStartBackward}
                  className="p-3 sm:p-1.5 rounded-lg hover:bg-accent active:bg-accent/80 active:scale-95 touch-manipulation min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0 flex items-center justify-center transition-all"
                  aria-label="Nudge start backward"
                >
                  <ChevronLeft className="w-5 h-5 sm:w-4 sm:h-4" />
                </button>
                <span className="text-sm font-mono w-16 text-center">
                  {formatTime(adjustedStart)}
                </span>
                <button
                  onClick={handleNudgeStartForward}
                  className="p-3 sm:p-1.5 rounded-lg hover:bg-accent active:bg-accent/80 active:scale-95 touch-manipulation min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0 flex items-center justify-center transition-all"
                  aria-label="Nudge start forward"
                >
                  <ChevronRight className="w-5 h-5 sm:w-4 sm:h-4" />
                </button>
              </div>

              <div className="flex items-center gap-3 sm:gap-2 justify-center sm:justify-start">
                <span className="text-xs text-muted-foreground">End:</span>
                <button
                  onClick={handleNudgeEndBackward}
                  className="p-3 sm:p-1.5 rounded-lg hover:bg-accent active:bg-accent/80 active:scale-95 touch-manipulation min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0 flex items-center justify-center transition-all"
                  aria-label="Nudge end backward"
                >
                  <ChevronLeft className="w-5 h-5 sm:w-4 sm:h-4" />
                </button>
                <span className="text-sm font-mono w-16 text-center">
                  {formatTime(adjustedEnd)}
                </span>
                <button
                  onClick={handleNudgeEndForward}
                  className="p-3 sm:p-1.5 rounded-lg hover:bg-accent active:bg-accent/80 active:scale-95 touch-manipulation min-w-[44px] min-h-[44px] sm:min-w-0 sm:min-h-0 flex items-center justify-center transition-all"
                  aria-label="Nudge end forward"
                >
                  <ChevronRight className="w-5 h-5 sm:w-4 sm:h-4" />
                </button>
              </div>

              <span className="text-xs text-muted-foreground text-center sm:text-left">
                Duration: {formatTime(adjustedEnd - adjustedStart)}
              </span>
            </div>

            {/* Keyboard shortcuts hint - desktop only */}
            <div className="hidden sm:block mt-2 text-xs text-muted-foreground">
              <span className="font-mono">Space</span> play/pause{' '}
              <span className="font-mono">J/K</span> nudge end{' '}
              <span className="font-mono">Shift+J/K</span> nudge start{' '}
              <span className="font-mono">C</span> confirm{' '}
              <span className="font-mono">X</span> reject{' '}
              <span className="font-mono">Esc</span> reset
              <br />
              <span className="font-mono">Click</span> seek{' '}
              <span className="font-mono">Shift+Click</span> set end{' '}
              <span className="font-mono">Alt+Click</span> set start
            </div>

            {/* Mobile mode toggle and instructions */}
            <div className="sm:hidden mt-3">
              <div className="flex gap-2 mb-2">
                <button
                  onClick={() => setTouchMode('seek')}
                  className={`flex-1 px-4 py-3 text-xs rounded-lg transition-all touch-manipulation min-h-[48px] active:scale-95 ${
                    touchMode === 'seek'
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-accent active:bg-accent/80'
                  }`}
                >
                  Seek
                </button>
                <button
                  onClick={() => setTouchMode('setStart')}
                  className={`flex-1 px-4 py-3 text-xs rounded-lg transition-all touch-manipulation min-h-[48px] active:scale-95 ${
                    touchMode === 'setStart'
                      ? 'bg-green-600 text-white'
                      : 'bg-muted text-muted-foreground hover:bg-accent active:bg-accent/80'
                  }`}
                >
                  Set Start
                </button>
                <button
                  onClick={() => setTouchMode('setEnd')}
                  className={`flex-1 px-4 py-3 text-xs rounded-lg transition-all touch-manipulation min-h-[48px] active:scale-95 ${
                    touchMode === 'setEnd'
                      ? 'bg-orange-600 text-white'
                      : 'bg-muted text-muted-foreground hover:bg-accent active:bg-accent/80'
                  }`}
                >
                  Set End
                </button>
              </div>
              <p className="text-xs text-muted-foreground text-center">
                {touchMode === 'seek' && 'Tap to seek. Double-tap = start. Long-press = end.'}
                {touchMode === 'setStart' && 'Tap segment to set START boundary'}
                {touchMode === 'setEnd' && 'Tap segment to set END boundary'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* SCROLLABLE: Transcript only - with swipe gestures for ad navigation */}
      <div
        ref={transcriptRef}
        className="flex-1 overflow-y-auto p-4 space-y-2 min-h-0"
        onTouchStart={handleSwipeStart}
        onTouchEnd={handleSwipeEnd}
      >
        {segments.map((segment, index) => {
          const inAd = isInAdRegion(segment.start, segment.end);
          const isCurrent = isCurrentSegment(segment.start, segment.end);
          const isStartBoundary = Math.abs(segment.start - adjustedStart) < 1;
          const isEndBoundary = Math.abs(segment.end - adjustedEnd) < 1;

          return (
            <div
              key={index}
              data-active={isCurrent}
              data-segment-start={segment.start}
              onClick={(e) => handleSegmentClick(segment, e)}
              onTouchStart={() => handleTouchStart(segment)}
              onTouchEnd={(e) => handleTouchEnd(segment, e)}
              onTouchCancel={handleTouchCancel}
              onTouchMove={handleTouchCancel}
              className={`p-3 sm:p-2 rounded-lg cursor-pointer transition-all select-none min-h-[44px] sm:min-h-0 flex items-start active:bg-accent/30 ${
                inAd
                  ? 'bg-red-500/20 hover:bg-red-500/30 active:bg-red-500/40'
                  : 'hover:bg-accent active:bg-accent/50'
              } ${isCurrent ? 'ring-2 ring-primary' : ''} ${
                isStartBoundary ? 'border-l-4 border-l-green-500' : ''
              } ${isEndBoundary ? 'border-r-4 border-r-orange-500' : ''}`}
            >
              <span className="text-xs text-muted-foreground font-mono mr-3 sm:mr-2 flex-shrink-0 pt-0.5">
                {formatTime(segment.start)}
              </span>
              <span className={inAd ? 'text-red-400' : ''}>{segment.text}</span>
            </div>
          );
        })}
      </div>

      {/* STICKY BOTTOM: Audio + Actions */}
      {/* Desktop: inline player */}
      <div className="hidden sm:block sticky bottom-0 z-20 bg-card border-t border-border flex-shrink-0">
        {audioUrl && (
          <div className="px-4 py-3 border-b border-border">
            <audio ref={audioRef} src={audioUrl} className="hidden" />
            <div className="flex items-center gap-3">
              <button
                onClick={handlePlayPause}
                className="p-2 rounded-full bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80 touch-manipulation"
                aria-label={isPlaying ? 'Pause' : 'Play'}
              >
                {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
              </button>
              <span className="text-sm font-mono w-12">{formatTime(currentTime)}</span>
              <div
                className="flex-1 h-2 bg-muted rounded-full overflow-hidden cursor-pointer hover:h-3 transition-all"
                onClick={handleProgressClick}
              >
                <div
                  className="h-full bg-primary pointer-events-none"
                  style={{ width: `${(currentTime / (segments[segments.length - 1]?.end || 1)) * 100}%` }}
                />
              </div>
            </div>
          </div>
        )}
        {/* Desktop action buttons with text - styled like mobile for visibility */}
        <div className="flex items-center justify-between gap-3 px-4 py-3 bg-muted/30">
          <div className="flex items-center gap-3">
            <button
              onClick={handleReset}
              disabled={saveStatus === 'saving'}
              className="px-4 py-2 text-sm font-medium rounded-lg border border-border bg-background hover:bg-accent disabled:opacity-50 transition-colors"
            >
              Reset
            </button>
            <button
              onClick={handleConfirm}
              disabled={saveStatus === 'saving'}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                saveStatus === 'saving'
                  ? 'bg-green-600/50 cursor-wait'
                  : saveStatus === 'success'
                  ? 'bg-green-600'
                  : 'bg-green-600 hover:bg-green-700'
              } text-white`}
            >
              {getConfirmButtonText()}
            </button>
            <button
              onClick={handleSave}
              disabled={saveStatus === 'saving'}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                saveStatus === 'saving'
                  ? 'bg-primary/50 cursor-wait'
                  : 'bg-primary hover:bg-primary/90'
              } text-primary-foreground`}
            >
              {getSaveButtonText()}
            </button>
          </div>
          {/* NOT AN AD button - prominent, larger, right side */}
          <button
            onClick={handleReject}
            disabled={saveStatus === 'saving'}
            className={`px-6 py-2.5 text-sm font-semibold rounded-lg transition-colors ${
              saveStatus === 'saving' ? 'bg-destructive/50 cursor-wait' : 'bg-destructive hover:bg-destructive/90 shadow-sm'
            } text-destructive-foreground`}
          >
            {getRejectButtonText()}
          </button>
        </div>
      </div>

      {/* Mobile: Bottom sheet audio player */}
      <div className="sm:hidden sticky bottom-0 z-20 bg-card border-t border-border flex-shrink-0">
        <audio ref={audioRef} src={audioUrl} className="hidden" />

        {/* Grab handle */}
        <button
          onClick={() => setAudioSheetExpanded(!audioSheetExpanded)}
          className="w-full flex justify-center py-2 touch-manipulation"
        >
          <div className="w-12 h-1 bg-muted-foreground/30 rounded-full" />
        </button>

        {/* Mini player (collapsed) */}
        {!audioSheetExpanded && (
          <div className="px-4 pb-3 space-y-3">
            <div className="flex items-center gap-3">
              <button
                onClick={handlePlayPause}
                className="p-3 rounded-full bg-primary text-primary-foreground active:scale-95 touch-manipulation min-w-[48px] min-h-[48px] flex items-center justify-center transition-all"
                aria-label={isPlaying ? 'Pause' : 'Play'}
              >
                {isPlaying ? <Pause className="w-6 h-6" /> : <Play className="w-6 h-6" />}
              </button>
              <span className="text-sm font-mono w-12">{formatTime(currentTime)}</span>
              {/* Draggable progress bar */}
              <div
                ref={progressBarRef}
                className={`flex-1 relative bg-muted rounded-full cursor-pointer touch-manipulation transition-all ${isDraggingProgress ? 'h-5' : 'h-4'}`}
                onClick={handleProgressClick}
                onTouchStart={handleProgressDragStart}
                onTouchMove={handleProgressDrag}
                onTouchEnd={handleProgressDragEnd}
              >
                <div
                  className="absolute top-0 left-0 h-full bg-primary rounded-full pointer-events-none"
                  style={{ width: `${(currentTime / (segments[segments.length - 1]?.end || 1)) * 100}%` }}
                />
                {/* Thumb indicator */}
                <div
                  className={`absolute top-1/2 -translate-y-1/2 bg-primary rounded-full shadow-md transition-all pointer-events-none ${isDraggingProgress ? 'w-6 h-6' : 'w-4 h-4'}`}
                  style={{ left: `calc(${(currentTime / (segments[segments.length - 1]?.end || 1)) * 100}% - ${isDraggingProgress ? '12px' : '8px'})` }}
                />
              </div>
            </div>
            {/* Action buttons with labels */}
            <div className="flex items-center justify-center gap-2">
              <button
                onClick={handleReject}
                disabled={saveStatus === 'saving'}
                className={`p-2 min-w-[56px] min-h-[56px] rounded-lg touch-manipulation active:scale-95 transition-all flex flex-col items-center justify-center gap-0.5 ${
                  saveStatus === 'saving' ? 'bg-destructive/50 cursor-wait' : saveStatus === 'success' ? 'bg-green-600' : saveStatus === 'error' ? 'bg-red-600' : 'bg-destructive/10 text-destructive active:bg-destructive/20'
                }`}
                title="Not an Ad"
              >
                <X className="w-4 h-4" />
                <span className="text-[10px] font-medium">Not Ad</span>
              </button>
              <button
                onClick={handleReset}
                disabled={saveStatus === 'saving'}
                className="p-2 min-w-[56px] min-h-[56px] rounded-lg bg-muted touch-manipulation active:scale-95 active:bg-accent transition-all flex flex-col items-center justify-center gap-0.5 disabled:opacity-50"
                title="Reset"
              >
                <RotateCcw className="w-4 h-4" />
                <span className="text-[10px] font-medium">Reset</span>
              </button>
              <button
                onClick={handleConfirm}
                disabled={saveStatus === 'saving'}
                className={`p-2 min-w-[56px] min-h-[56px] rounded-lg touch-manipulation active:scale-95 transition-all flex flex-col items-center justify-center gap-0.5 ${
                  saveStatus === 'saving' ? 'bg-green-600/50 cursor-wait' : saveStatus === 'success' ? 'bg-green-600' : saveStatus === 'error' ? 'bg-red-600' : 'bg-green-600 text-white active:bg-green-700'
                }`}
                title="Confirm"
              >
                <Check className="w-4 h-4" />
                <span className="text-[10px] font-medium">Confirm</span>
              </button>
              <button
                onClick={handleSave}
                disabled={saveStatus === 'saving'}
                className={`p-2 min-w-[56px] min-h-[56px] rounded-lg touch-manipulation active:scale-95 transition-all flex flex-col items-center justify-center gap-0.5 ${
                  saveStatus === 'saving' ? 'bg-primary/50 cursor-wait' : saveStatus === 'success' ? 'bg-green-600' : saveStatus === 'error' ? 'bg-red-600' : 'bg-primary text-primary-foreground active:bg-primary/90'
                }`}
                title="Save Adjusted"
              >
                <Save className="w-4 h-4" />
                <span className="text-[10px] font-medium">Save</span>
              </button>
            </div>
          </div>
        )}

        {/* Expanded player */}
        {audioSheetExpanded && (
          <div className="px-4 pb-4 space-y-4">
            {/* Large progress bar */}
            <div
              ref={progressBarRef}
              className={`relative bg-muted rounded-full cursor-pointer touch-manipulation transition-all ${isDraggingProgress ? 'h-6' : 'h-5'}`}
              onClick={handleProgressClick}
              onTouchStart={handleProgressDragStart}
              onTouchMove={handleProgressDrag}
              onTouchEnd={handleProgressDragEnd}
            >
              <div
                className="absolute top-0 left-0 h-full bg-primary rounded-full pointer-events-none"
                style={{ width: `${(currentTime / (segments[segments.length - 1]?.end || 1)) * 100}%` }}
              />
              <div
                className={`absolute top-1/2 -translate-y-1/2 bg-primary rounded-full shadow-md transition-all pointer-events-none ${isDraggingProgress ? 'w-7 h-7' : 'w-5 h-5'}`}
                style={{ left: `calc(${(currentTime / (segments[segments.length - 1]?.end || 1)) * 100}% - ${isDraggingProgress ? '14px' : '10px'})` }}
              />
            </div>

            {/* Time display */}
            <div className="flex justify-between text-sm text-muted-foreground font-mono">
              <span>{formatTime(currentTime)}</span>
              <span>{formatTime(segments[segments.length - 1]?.end || 0)}</span>
            </div>

            {/* Large play controls */}
            <div className="flex items-center justify-center gap-4">
              <button onClick={goToPreviousAd} className="p-3 rounded-full bg-muted active:bg-accent touch-manipulation min-w-[48px] min-h-[48px] flex items-center justify-center active:scale-95 transition-all">
                <ChevronLeft className="w-6 h-6" />
              </button>
              <button
                onClick={handlePlayPause}
                className="p-4 rounded-full bg-primary text-primary-foreground active:scale-95 touch-manipulation min-w-[64px] min-h-[64px] flex items-center justify-center transition-all"
              >
                {isPlaying ? <Pause className="w-8 h-8" /> : <Play className="w-8 h-8" />}
              </button>
              <button onClick={goToNextAd} className="p-3 rounded-full bg-muted active:bg-accent touch-manipulation min-w-[48px] min-h-[48px] flex items-center justify-center active:scale-95 transition-all">
                <ChevronRight className="w-6 h-6" />
              </button>
            </div>

            {/* Action buttons with labels in expanded mode */}
            <div className="flex items-center justify-center gap-2">
              <button onClick={handleReject} disabled={saveStatus === 'saving'} className="flex-1 py-3 rounded-lg bg-destructive/10 text-destructive text-sm font-medium touch-manipulation active:scale-95 transition-all">Not Ad</button>
              <button onClick={handleReset} disabled={saveStatus === 'saving'} className="flex-1 py-3 rounded-lg bg-muted text-sm font-medium touch-manipulation active:scale-95 transition-all">Reset</button>
              <button onClick={handleConfirm} disabled={saveStatus === 'saving'} className="flex-1 py-3 rounded-lg bg-green-600 text-white text-sm font-medium touch-manipulation active:scale-95 transition-all">Confirm</button>
              <button onClick={handleSave} disabled={saveStatus === 'saving'} className="flex-1 py-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium touch-manipulation active:scale-95 transition-all">Save</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default TranscriptEditor;
