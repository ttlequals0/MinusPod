import { useState, useRef, useEffect, useCallback } from 'react';
import { useTranscriptKeyboard } from '../hooks/useTranscriptKeyboard';

interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

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
}: TranscriptEditorProps) {
  const [selectedAdIndex, setSelectedAdIndex] = useState(0);
  const [adjustedStart, setAdjustedStart] = useState(0);
  const [adjustedEnd, setAdjustedEnd] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  const NUDGE_AMOUNT = 0.5; // seconds

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

  // Auto-scroll transcript to current time
  useEffect(() => {
    if (!transcriptRef.current) return;

    const activeSegment = transcriptRef.current.querySelector('[data-active="true"]');
    if (activeSegment) {
      activeSegment.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [currentTime]);

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
    if (!selectedAd) return;

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
  }, [selectedAd, adjustedStart, adjustedEnd, onCorrection, selectedAdIndex, detectedAds.length]);

  const handleReset = useCallback(() => {
    if (selectedAd) {
      setAdjustedStart(selectedAd.start);
      setAdjustedEnd(selectedAd.end);
    }
  }, [selectedAd]);

  const handleConfirm = useCallback(() => {
    if (!selectedAd) return;
    onCorrection({
      type: 'confirm',
      originalAd: selectedAd,
    });
    if (selectedAdIndex < detectedAds.length - 1) {
      setSelectedAdIndex(selectedAdIndex + 1);
    }
  }, [selectedAd, onCorrection, selectedAdIndex, detectedAds.length]);

  const handleReject = useCallback(() => {
    if (!selectedAd) return;
    onCorrection({
      type: 'reject',
      originalAd: selectedAd,
    });
    if (selectedAdIndex < detectedAds.length - 1) {
      setSelectedAdIndex(selectedAdIndex + 1);
    }
  }, [selectedAd, onCorrection, selectedAdIndex, detectedAds.length]);

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

  const isInAdRegion = (segStart: number, segEnd: number) => {
    return segStart < adjustedEnd && segEnd > adjustedStart;
  };

  const isCurrentSegment = (segStart: number, segEnd: number) => {
    return currentTime >= segStart && currentTime < segEnd;
  };

  if (!selectedAd) {
    return (
      <div className="p-4 text-center text-muted-foreground">
        No ads to review
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-card rounded-lg border border-border">
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

      {/* Ad selector */}
      <div className="flex gap-1 px-4 py-2 border-b border-border overflow-x-auto">
        {detectedAds.map((ad, index) => (
          <button
            key={index}
            onClick={() => setSelectedAdIndex(index)}
            className={`px-2 py-1 text-xs rounded whitespace-nowrap ${
              index === selectedAdIndex
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted hover:bg-accent'
            }`}
          >
            {formatTime(ad.start)} - {formatTime(ad.end)}
          </button>
        ))}
      </div>

      {/* Boundary controls */}
      <div className="px-4 py-3 border-b border-border bg-muted/30">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Start:</span>
            <button
              onClick={handleNudgeStartBackward}
              className="p-1 rounded hover:bg-accent"
              aria-label="Nudge start backward"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            <span className="text-sm font-mono w-16 text-center">
              {formatTime(adjustedStart)}
            </span>
            <button
              onClick={handleNudgeStartForward}
              className="p-1 rounded hover:bg-accent"
              aria-label="Nudge start forward"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">End:</span>
            <button
              onClick={handleNudgeEndBackward}
              className="p-1 rounded hover:bg-accent"
              aria-label="Nudge end backward"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            <span className="text-sm font-mono w-16 text-center">
              {formatTime(adjustedEnd)}
            </span>
            <button
              onClick={handleNudgeEndForward}
              className="p-1 rounded hover:bg-accent"
              aria-label="Nudge end forward"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </div>

          <span className="text-xs text-muted-foreground">
            Duration: {formatTime(adjustedEnd - adjustedStart)}
          </span>
        </div>

        {/* Keyboard shortcuts hint */}
        <div className="mt-2 text-xs text-muted-foreground">
          <span className="font-mono">Space</span> play/pause{' '}
          <span className="font-mono">J/K</span> nudge end{' '}
          <span className="font-mono">Shift+J/K</span> nudge start{' '}
          <span className="font-mono">C</span> confirm{' '}
          <span className="font-mono">X</span> reject{' '}
          <span className="font-mono">Esc</span> reset
        </div>
      </div>

      {/* Transcript */}
      <div
        ref={transcriptRef}
        className="flex-1 overflow-y-auto p-4 space-y-1"
      >
        {segments.map((segment, index) => {
          const inAd = isInAdRegion(segment.start, segment.end);
          const isCurrent = isCurrentSegment(segment.start, segment.end);

          return (
            <div
              key={index}
              data-active={isCurrent}
              onClick={() => seekTo(segment.start)}
              className={`p-2 rounded cursor-pointer transition-colors ${
                inAd
                  ? 'bg-red-500/20 hover:bg-red-500/30'
                  : 'hover:bg-accent'
              } ${isCurrent ? 'ring-2 ring-primary' : ''}`}
            >
              <span className="text-xs text-muted-foreground font-mono mr-2">
                {formatTime(segment.start)}
              </span>
              <span className={inAd ? 'text-red-400' : ''}>{segment.text}</span>
            </div>
          );
        })}
      </div>

      {/* Audio player */}
      {audioUrl && (
        <div className="px-4 py-3 border-t border-border">
          <audio ref={audioRef} src={audioUrl} className="hidden" />
          <div className="flex items-center gap-3">
            <button
              onClick={handlePlayPause}
              className="p-2 rounded-full bg-primary text-primary-foreground hover:bg-primary/90"
              aria-label={isPlaying ? 'Pause' : 'Play'}
            >
              {isPlaying ? (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M6 4h4v16H6V4zm8 0h4v16h-4V4z" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M8 5v14l11-7z" />
                </svg>
              )}
            </button>
            <span className="text-sm font-mono">{formatTime(currentTime)}</span>
            <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-primary"
                style={{
                  width: `${(currentTime / (segments[segments.length - 1]?.end || 1)) * 100}%`,
                }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-border bg-muted/30">
        <button
          onClick={handleReject}
          className="px-4 py-2 text-sm bg-destructive text-destructive-foreground rounded hover:bg-destructive/90"
        >
          Not an Ad
        </button>

        <div className="flex items-center gap-2">
          <button
            onClick={handleReset}
            className="px-4 py-2 text-sm bg-muted text-muted-foreground rounded hover:bg-accent"
          >
            Reset
          </button>
          <button
            onClick={handleConfirm}
            className="px-4 py-2 text-sm bg-green-600 text-white rounded hover:bg-green-700"
          >
            Confirm
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90"
          >
            Save Adjusted
          </button>
        </div>
      </div>
    </div>
  );
}

export default TranscriptEditor;
