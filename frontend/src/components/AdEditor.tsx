import { useState, useRef, useEffect, useCallback } from 'react';
import { useTranscriptKeyboard } from '../hooks/useTranscriptKeyboard';
import {
  AdHeader,
  AdSelector,
  ReasonPanel,
  AudioPlayer,
  BoundaryControls,
  ActionButtons,
  MobileAudioSheet,
} from './ad-editor';
import type { DetectedAd, AdCorrection, SaveStatus } from './ad-editor';

interface AdEditorProps {
  detectedAds: DetectedAd[];
  audioDuration: number;
  audioUrl?: string;
  onCorrection: (correction: AdCorrection) => void;
  onClose?: () => void;
  initialSeekTime?: number;
  saveStatus?: SaveStatus;
  selectedAdIndex?: number;
  onSelectedAdIndexChange?: (index: number) => void;
}

// Re-export types for consumers
export type { AdCorrection };

export function AdEditor({
  detectedAds,
  audioDuration,
  audioUrl,
  onCorrection,
  onClose,
  initialSeekTime,
  saveStatus = 'idle',
  selectedAdIndex: externalSelectedAdIndex,
  onSelectedAdIndexChange,
}: AdEditorProps) {
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
  const [startAdjustment, setStartAdjustment] = useState(0);
  const [endAdjustment, setEndAdjustment] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [audioSheetExpanded, setAudioSheetExpanded] = useState(false);
  const [isDraggingProgress, setIsDraggingProgress] = useState(false);
  const [preserveSeekPosition, setPreserveSeekPosition] = useState(false);
  const seekingFromJumpRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const progressBarRef = useRef<HTMLDivElement>(null);

  // Haptic feedback helper
  const triggerHaptic = useCallback((style: 'light' | 'medium' | 'heavy' = 'light') => {
    if ('vibrate' in navigator) {
      navigator.vibrate(style === 'light' ? 10 : style === 'medium' ? 20 : 30);
    }
  }, []);

  const NUDGE_AMOUNT = 0.5;
  const selectedAd = detectedAds[selectedAdIndex];

  // Initialize adjusted bounds and seek audio when ad changes
  useEffect(() => {
    if (selectedAd) {
      setAdjustedStart(selectedAd.start);
      setAdjustedEnd(selectedAd.end);
      setStartAdjustment(0);
      setEndAdjustment(0);
      if (audioRef.current && !seekingFromJumpRef.current) {
        audioRef.current.currentTime = selectedAd.start;
      }
      seekingFromJumpRef.current = false;
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

  // Handle initial seek time (from Jump button)
  useEffect(() => {
    if (initialSeekTime !== undefined && audioRef.current) {
      const adIndex = detectedAds.findIndex(
        (ad) => Math.abs(initialSeekTime - ad.start) < 0.5 ||
                (initialSeekTime > ad.start && initialSeekTime <= ad.end)
      );
      if (adIndex !== -1) {
        seekingFromJumpRef.current = true;
        setSelectedAdIndex(adIndex);
      }
      audioRef.current.currentTime = initialSeekTime;
      setPreserveSeekPosition(true);
    }
  }, [initialSeekTime, detectedAds]);

  // Auto-focus container when editor opens for keyboard shortcuts
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.focus();
    }
  }, []);

  // Handle ad selection with haptic feedback
  const handleAdSelect = useCallback((index: number) => {
    setSelectedAdIndex(index);
    triggerHaptic('light');
  }, [triggerHaptic]);

  // Navigate to previous/next ad (for swipe gestures)
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

  const handlePlayPause = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;

    if (isPlaying) {
      audio.pause();
    } else {
      if (!preserveSeekPosition && (currentTime < adjustedStart || currentTime > adjustedEnd)) {
        audio.currentTime = adjustedStart;
      }
      setPreserveSeekPosition(false);
      audio.play();
    }
  }, [isPlaying, currentTime, adjustedStart, adjustedEnd, preserveSeekPosition]);

  const handleNudgeEndForward = useCallback(() => {
    setAdjustedEnd((prev) => Math.min(prev + NUDGE_AMOUNT, audioDuration || prev));
    triggerHaptic('light');
  }, [audioDuration, triggerHaptic]);

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

  const handleProgressClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const percentage = clickX / rect.width;
    const duration = audioDuration || 1;
    seekTo(percentage * duration);
  };

  const handleProgressDragStart = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    setIsDraggingProgress(true);
    triggerHaptic('light');
    const rect = e.currentTarget.getBoundingClientRect();
    const touchX = e.touches[0].clientX - rect.left;
    const percentage = Math.max(0, Math.min(1, touchX / rect.width));
    const duration = audioDuration || 1;
    seekTo(percentage * duration);
  }, [audioDuration, triggerHaptic]);

  const handleProgressDrag = useCallback((e: React.TouchEvent<HTMLDivElement>) => {
    if (!isDraggingProgress) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const touchX = e.touches[0].clientX - rect.left;
    const percentage = Math.max(0, Math.min(1, touchX / rect.width));
    const duration = audioDuration || 1;
    seekTo(percentage * duration);
  }, [isDraggingProgress, audioDuration]);

  const handleProgressDragEnd = useCallback(() => {
    setIsDraggingProgress(false);
  }, []);

  // Boundary adjustment handlers (unified for all variants)
  const handleStartAdjustmentChange = useCallback((newAdj: number) => {
    setStartAdjustment(newAdj);
    const newStart = Math.max(0, (selectedAd?.start || 0) + newAdj);
    setAdjustedStart(newStart);
  }, [selectedAd]);

  const handleEndAdjustmentChange = useCallback((newAdj: number) => {
    setEndAdjustment(newAdj);
    const maxEnd = audioDuration || 9999;
    const newEnd = Math.min(maxEnd, (selectedAd?.end || 0) + newAdj);
    setAdjustedEnd(newEnd);
  }, [selectedAd, audioDuration]);

  // Button text helpers
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
      className="flex flex-col max-h-[85dvh] sm:max-h-[80vh] bg-card rounded-lg border border-border outline-none focus:ring-2 focus:ring-primary/50 overflow-hidden"
    >
      {/* TOP: Header + Ad Selector */}
      <div className="bg-card shrink-0">
        <AdHeader
          selectedAdIndex={selectedAdIndex}
          totalAds={detectedAds.length}
          selectedAd={selectedAd}
          onClose={onClose}
        />
        <AdSelector
          detectedAds={detectedAds}
          selectedAdIndex={selectedAdIndex}
          onAdSelect={handleAdSelect}
        />
      </div>

      {/* Reason panel */}
      <ReasonPanel selectedAd={selectedAd} />

      {/* Desktop bottom bar */}
      <div className="hidden sm:block bg-card border-t border-border shrink-0">
        {audioUrl && (
          <AudioPlayer
            audioUrl={audioUrl}
            audioRef={audioRef}
            isPlaying={isPlaying}
            currentTime={currentTime}
            audioDuration={audioDuration}
            onPlayPause={handlePlayPause}
            onProgressClick={handleProgressClick}
          />
        )}

        <BoundaryControls
          variant="desktop"
          selectedAd={selectedAd}
          adjustedStart={adjustedStart}
          adjustedEnd={adjustedEnd}
          startAdjustment={startAdjustment}
          endAdjustment={endAdjustment}
          onStartAdjustmentChange={handleStartAdjustmentChange}
          onEndAdjustmentChange={handleEndAdjustmentChange}
          triggerHaptic={() => triggerHaptic()}
        />

        <ActionButtons
          variant="desktop"
          saveStatus={saveStatus}
          onSave={handleSave}
          onConfirm={handleConfirm}
          onReject={handleReject}
          onReset={handleReset}
          getSaveButtonText={getSaveButtonText}
          getConfirmButtonText={getConfirmButtonText}
          getRejectButtonText={getRejectButtonText}
        />
      </div>

      {/* Mobile: Bottom sheet audio player */}
      <MobileAudioSheet
        audioUrl={audioUrl}
        audioRef={audioRef}
        isPlaying={isPlaying}
        currentTime={currentTime}
        audioDuration={audioDuration}
        adjustedStart={adjustedStart}
        adjustedEnd={adjustedEnd}
        startAdjustment={startAdjustment}
        endAdjustment={endAdjustment}
        selectedAd={selectedAd}
        saveStatus={saveStatus}
        audioSheetExpanded={audioSheetExpanded}
        isDraggingProgress={isDraggingProgress}
        progressBarRef={progressBarRef}
        onToggleExpanded={() => setAudioSheetExpanded(!audioSheetExpanded)}
        onPlayPause={handlePlayPause}
        onProgressClick={handleProgressClick}
        onProgressDragStart={handleProgressDragStart}
        onProgressDrag={handleProgressDrag}
        onProgressDragEnd={handleProgressDragEnd}
        onStartAdjustmentChange={handleStartAdjustmentChange}
        onEndAdjustmentChange={handleEndAdjustmentChange}
        triggerHaptic={() => triggerHaptic()}
        onSave={handleSave}
        onConfirm={handleConfirm}
        onReject={handleReject}
        onReset={handleReset}
        onGoToPreviousAd={goToPreviousAd}
        onGoToNextAd={goToNextAd}
        getSaveButtonText={getSaveButtonText}
        getConfirmButtonText={getConfirmButtonText}
        getRejectButtonText={getRejectButtonText}
      />
    </div>
  );
}

export default AdEditor;
