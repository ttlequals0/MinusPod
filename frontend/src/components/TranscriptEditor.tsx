import { useState, useRef, useEffect, useCallback } from 'react';
import { useTranscriptKeyboard } from '../hooks/useTranscriptKeyboard';

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
}

interface TranscriptEditorProps {
  segments: TranscriptSegment[];
  detectedAds: DetectedAd[];
  audioUrl?: string;
  onCorrection: (correction: AdCorrection) => void;
  onClose?: () => void;
  initialSeekTime?: number;
  saveStatus?: SaveStatus;
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
}: TranscriptEditorProps) {
  const [selectedAdIndex, setSelectedAdIndex] = useState(0);
  const [adjustedStart, setAdjustedStart] = useState(0);
  const [adjustedEnd, setAdjustedEnd] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [touchMode, setTouchMode] = useState<TouchMode>('seek');
  const [lastTapTime, setLastTapTime] = useState(0);
  const [longPressTimer, setLongPressTimer] = useState<ReturnType<typeof setTimeout> | null>(null);
  const [mobileControlsExpanded, setMobileControlsExpanded] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

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

  // Handle initial seek time (from Jump button)
  useEffect(() => {
    if (initialSeekTime !== undefined && audioRef.current) {
      // Find the ad that contains this time
      const adIndex = detectedAds.findIndex(
        (ad) => initialSeekTime >= ad.start && initialSeekTime <= ad.end
      );
      if (adIndex !== -1) {
        setSelectedAdIndex(adIndex);
      }
      // Seek to the time
      audioRef.current.currentTime = initialSeekTime;
    }
  }, [initialSeekTime, detectedAds]);

  // Auto-focus container when editor opens for keyboard shortcuts
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.focus();
    }
  }, []);

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

  // Handle ad selection with auto-scroll
  const handleAdSelect = useCallback((index: number) => {
    setSelectedAdIndex(index);
    const ad = detectedAds[index];
    if (ad) {
      // Small delay to allow state update before scroll
      setTimeout(() => scrollToAd(ad), 50);
    }
  }, [detectedAds, scrollToAd]);

  const handlePlayPause = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
    } else {
      // Seek to ad start if not within ad
      if (currentTime < adjustedStart || currentTime > adjustedEnd) {
        audio.currentTime = adjustedStart;
      }
      audio.play();
    }
  }, [isPlaying, currentTime, adjustedStart, adjustedEnd]);

  const handleNudgeEndForward = useCallback(() => {
    setAdjustedEnd((prev) => Math.min(prev + NUDGE_AMOUNT, segments[segments.length - 1]?.end || prev));
  }, [segments]);

  const handleNudgeEndBackward = useCallback(() => {
    setAdjustedEnd((prev) => Math.max(prev - NUDGE_AMOUNT, adjustedStart + 1));
  }, [adjustedStart]);

  const handleNudgeStartForward = useCallback(() => {
    setAdjustedStart((prev) => Math.min(prev + NUDGE_AMOUNT, adjustedEnd - 1));
  }, [adjustedEnd]);

  const handleNudgeStartBackward = useCallback(() => {
    setAdjustedStart((prev) => Math.max(prev - NUDGE_AMOUNT, 0));
  }, []);

  const handleSave = useCallback(() => {
    if (!selectedAd || saveStatus === 'saving') return;

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
  }, [selectedAd, adjustedStart, adjustedEnd, onCorrection, selectedAdIndex, detectedAds.length, saveStatus]);

  const handleReset = useCallback(() => {
    if (selectedAd) {
      setAdjustedStart(selectedAd.start);
      setAdjustedEnd(selectedAd.end);
    }
  }, [selectedAd]);

  const handleConfirm = useCallback(() => {
    if (!selectedAd || saveStatus === 'saving') return;
    onCorrection({
      type: 'confirm',
      originalAd: selectedAd,
    });
    if (selectedAdIndex < detectedAds.length - 1) {
      setSelectedAdIndex(selectedAdIndex + 1);
    }
  }, [selectedAd, onCorrection, selectedAdIndex, detectedAds.length, saveStatus]);

  const handleReject = useCallback(() => {
    if (!selectedAd || saveStatus === 'saving') return;
    onCorrection({
      type: 'reject',
      originalAd: selectedAd,
    });
    if (selectedAdIndex < detectedAds.length - 1) {
      setSelectedAdIndex(selectedAdIndex + 1);
    }
  }, [selectedAd, onCorrection, selectedAdIndex, detectedAds.length, saveStatus]);

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
      className="flex flex-col h-[70vh] sm:h-[70vh] max-h-[500px] sm:max-h-[800px] bg-card rounded-lg border border-border outline-none focus:ring-2 focus:ring-primary/50 overflow-hidden"
    >
      {/* STICKY TOP: Header, Ad Selector, Boundary Controls */}
      <div className="sticky top-0 z-20 bg-card flex-shrink-0">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-4">
            <h3 className="text-sm font-medium">
              Ad {selectedAdIndex + 1} of {detectedAds.length}
            </h3>
            {selectedAd.sponsor && (
              <span className="px-2 py-0.5 text-xs bg-primary/10 text-primary rounded">
                {selectedAd.sponsor}
              </span>
            )}
            {selectedAd.detection_stage && (
              <span className="px-2 py-0.5 text-xs bg-muted text-muted-foreground rounded">
                {selectedAd.detection_stage}
              </span>
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

        {/* Ad selector - with momentum scrolling for mobile */}
        <div className="flex gap-1 px-4 py-2 border-b border-border overflow-x-auto scroll-smooth touch-pan-x">
          {detectedAds.map((ad, index) => (
            <button
              key={index}
              onClick={() => handleAdSelect(index)}
              className={`px-3 py-2 sm:px-2 sm:py-1 text-sm sm:text-xs rounded whitespace-nowrap touch-manipulation ${
                index === selectedAdIndex
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted hover:bg-accent'
              }`}
            >
              {formatTime(ad.start)} - {formatTime(ad.end)}
            </button>
          ))}
        </div>

        {/* Boundary controls - Collapsible on mobile */}
        <div className="border-b border-border bg-muted/30">
          {/* Mobile toggle button - shows current bounds, hidden on sm+ */}
          <button
            onClick={() => setMobileControlsExpanded(!mobileControlsExpanded)}
            className="w-full px-4 py-2 flex items-center justify-between sm:hidden touch-manipulation"
          >
            <span className="text-sm font-mono text-muted-foreground">
              {formatTime(adjustedStart)} - {formatTime(adjustedEnd)}
            </span>
            <span className="text-xs text-primary">
              {mobileControlsExpanded ? 'Hide Controls' : 'Adjust Boundaries'}
            </span>
          </button>

          {/* Controls - always visible on sm+, conditionally on mobile */}
          <div className={`px-4 py-3 ${mobileControlsExpanded ? 'block' : 'hidden'} sm:block`}>
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
              <div className="flex items-center gap-2 justify-center sm:justify-start">
                <span className="text-xs text-muted-foreground">Start:</span>
                <button
                  onClick={handleNudgeStartBackward}
                  className="p-2 sm:p-1 rounded hover:bg-accent active:bg-accent/80 touch-manipulation"
                  aria-label="Nudge start backward"
                >
                  <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                  </svg>
                </button>
                <span className="text-sm font-mono w-16 text-center">
                  {formatTime(adjustedStart)}
                </span>
                <button
                  onClick={handleNudgeStartForward}
                  className="p-2 sm:p-1 rounded hover:bg-accent active:bg-accent/80 touch-manipulation"
                  aria-label="Nudge start forward"
                >
                  <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </button>
              </div>

              <div className="flex items-center gap-2 justify-center sm:justify-start">
                <span className="text-xs text-muted-foreground">End:</span>
                <button
                  onClick={handleNudgeEndBackward}
                  className="p-2 sm:p-1 rounded hover:bg-accent active:bg-accent/80 touch-manipulation"
                  aria-label="Nudge end backward"
                >
                  <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                  </svg>
                </button>
                <span className="text-sm font-mono w-16 text-center">
                  {formatTime(adjustedEnd)}
                </span>
                <button
                  onClick={handleNudgeEndForward}
                  className="p-2 sm:p-1 rounded hover:bg-accent active:bg-accent/80 touch-manipulation"
                  aria-label="Nudge end forward"
                >
                  <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
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
                  className={`flex-1 px-3 py-2 text-xs rounded-md transition-colors touch-manipulation ${
                    touchMode === 'seek'
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground hover:bg-accent'
                  }`}
                >
                  Seek Mode
                </button>
                <button
                  onClick={() => setTouchMode('setStart')}
                  className={`flex-1 px-3 py-2 text-xs rounded-md transition-colors touch-manipulation ${
                    touchMode === 'setStart'
                      ? 'bg-green-600 text-white'
                      : 'bg-muted text-muted-foreground hover:bg-accent'
                  }`}
                >
                  Set Start
                </button>
                <button
                  onClick={() => setTouchMode('setEnd')}
                  className={`flex-1 px-3 py-2 text-xs rounded-md transition-colors touch-manipulation ${
                    touchMode === 'setEnd'
                      ? 'bg-orange-600 text-white'
                      : 'bg-muted text-muted-foreground hover:bg-accent'
                  }`}
                >
                  Set End
                </button>
              </div>
              <p className="text-xs text-muted-foreground text-center">
                {touchMode === 'seek' && 'Tap segment to seek. Double-tap = set start. Long-press = set end.'}
                {touchMode === 'setStart' && 'Tap any segment to set as START boundary'}
                {touchMode === 'setEnd' && 'Tap any segment to set as END boundary'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* SCROLLABLE: Transcript only */}
      <div
        ref={transcriptRef}
        className="flex-1 overflow-y-auto p-4 space-y-1 min-h-0"
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
              className={`p-2 rounded cursor-pointer transition-colors select-none ${
                inAd
                  ? 'bg-red-500/20 hover:bg-red-500/30'
                  : 'hover:bg-accent'
              } ${isCurrent ? 'ring-2 ring-primary' : ''} ${
                isStartBoundary ? 'border-l-4 border-l-green-500' : ''
              } ${isEndBoundary ? 'border-r-4 border-r-orange-500' : ''}`}
            >
              <span className="text-xs text-muted-foreground font-mono mr-2">
                {formatTime(segment.start)}
              </span>
              <span className={inAd ? 'text-red-400' : ''}>{segment.text}</span>
            </div>
          );
        })}
      </div>

      {/* STICKY BOTTOM: Audio + Actions */}
      <div className="sticky bottom-0 z-20 bg-card border-t border-border flex-shrink-0">
        {/* Audio player - Mobile optimized */}
        {audioUrl && (
          <div className="px-4 py-3 border-b border-border">
            <audio ref={audioRef} src={audioUrl} className="hidden" />
            <div className="flex items-center gap-3">
              <button
                onClick={handlePlayPause}
                className="p-3 sm:p-2 rounded-full bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80 touch-manipulation"
                aria-label={isPlaying ? 'Pause' : 'Play'}
              >
                {isPlaying ? (
                  <svg className="w-6 h-6 sm:w-5 sm:h-5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
                  </svg>
                ) : (
                  <svg className="w-6 h-6 sm:w-5 sm:h-5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                )}
              </button>
              <span className="text-sm font-mono">{formatTime(currentTime)}</span>
              <div
                className="flex-1 h-3 sm:h-2 bg-muted rounded-full overflow-hidden cursor-pointer hover:h-4 sm:hover:h-3 transition-all touch-manipulation"
                onClick={handleProgressClick}
                title="Click to seek"
              >
                <div
                  className="h-full bg-primary pointer-events-none"
                  style={{
                    width: `${(currentTime / (segments[segments.length - 1]?.end || 1)) * 100}%`,
                  }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Action buttons - Horizontal layout, compact on mobile */}
        <div className="flex flex-row flex-wrap items-center justify-between gap-2 px-4 py-2 sm:py-3 bg-muted/30">
          <button
            onClick={handleReject}
            disabled={saveStatus === 'saving'}
            className={`px-3 py-2 sm:px-4 sm:py-2 text-xs sm:text-sm rounded touch-manipulation transition-colors ${
              saveStatus === 'saving'
                ? 'bg-destructive/50 text-destructive-foreground cursor-wait'
                : saveStatus === 'success'
                ? 'bg-green-600 text-white'
                : saveStatus === 'error'
                ? 'bg-red-600 text-white'
                : 'bg-destructive text-destructive-foreground hover:bg-destructive/90 active:bg-destructive/80'
            }`}
          >
            {getRejectButtonText()}
          </button>

          <div className="flex items-center gap-1 sm:gap-2">
            <button
              onClick={handleReset}
              disabled={saveStatus === 'saving'}
              className="px-3 py-2 sm:px-4 sm:py-2 text-xs sm:text-sm bg-muted text-muted-foreground rounded hover:bg-accent active:bg-accent/80 touch-manipulation disabled:opacity-50"
            >
              Reset
            </button>
            <button
              onClick={handleConfirm}
              disabled={saveStatus === 'saving'}
              className={`px-3 py-2 sm:px-4 sm:py-2 text-xs sm:text-sm rounded touch-manipulation transition-colors ${
                saveStatus === 'saving'
                  ? 'bg-green-600/50 text-white cursor-wait'
                  : saveStatus === 'success'
                  ? 'bg-green-600 text-white'
                  : saveStatus === 'error'
                  ? 'bg-red-600 text-white'
                  : 'bg-green-600 text-white hover:bg-green-700 active:bg-green-800'
              }`}
            >
              {getConfirmButtonText()}
            </button>
            <button
              onClick={handleSave}
              disabled={saveStatus === 'saving'}
              className={`px-3 py-2 sm:px-4 sm:py-2 text-xs sm:text-sm rounded touch-manipulation transition-colors ${
                saveStatus === 'saving'
                  ? 'bg-primary/50 text-primary-foreground cursor-wait'
                  : saveStatus === 'success'
                  ? 'bg-green-600 text-white'
                  : saveStatus === 'error'
                  ? 'bg-red-600 text-white'
                  : 'bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80'
              }`}
            >
              {getSaveButtonText()}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default TranscriptEditor;
