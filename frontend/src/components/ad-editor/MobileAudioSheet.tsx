import React from 'react';
import { Play, Pause, ChevronLeft, ChevronRight } from 'lucide-react';
import { DetectedAd, SaveStatus, formatTime } from './types';
import { BoundaryControls } from './BoundaryControls';
import { ActionButtons } from './ActionButtons';

interface MobileAudioSheetProps {
  audioUrl?: string;
  audioRef: React.Ref<HTMLAudioElement>;
  isPlaying: boolean;
  currentTime: number;
  audioDuration: number;
  adjustedStart: number;
  adjustedEnd: number;
  startAdjustment: number;
  endAdjustment: number;
  selectedAd: DetectedAd;
  saveStatus: SaveStatus;
  audioSheetExpanded: boolean;
  isDraggingProgress: boolean;
  progressBarRef: React.Ref<HTMLDivElement>;
  onToggleExpanded: () => void;
  onPlayPause: () => void;
  onProgressClick: (e: React.MouseEvent<HTMLDivElement>) => void;
  onProgressDragStart: (e: React.TouchEvent<HTMLDivElement>) => void;
  onProgressDrag: (e: React.TouchEvent<HTMLDivElement>) => void;
  onProgressDragEnd: () => void;
  onStartAdjustmentChange: (newAdj: number) => void;
  onEndAdjustmentChange: (newAdj: number) => void;
  triggerHaptic: () => void;
  onSave: () => void;
  onConfirm: () => void;
  onReject: () => void;
  onReset: () => void;
  onGoToPreviousAd: () => void;
  onGoToNextAd: () => void;
  getSaveButtonText: () => string;
  getConfirmButtonText: () => string;
  getRejectButtonText: () => string;
}

export function MobileAudioSheet({
  audioUrl,
  audioRef,
  isPlaying,
  currentTime,
  audioDuration,
  adjustedStart,
  adjustedEnd,
  startAdjustment,
  endAdjustment,
  selectedAd,
  saveStatus,
  audioSheetExpanded,
  isDraggingProgress,
  progressBarRef,
  onToggleExpanded,
  onPlayPause,
  onProgressClick,
  onProgressDragStart,
  onProgressDrag,
  onProgressDragEnd,
  onStartAdjustmentChange,
  onEndAdjustmentChange,
  triggerHaptic,
  onSave,
  onConfirm,
  onReject,
  onReset,
  onGoToPreviousAd,
  onGoToNextAd,
  getSaveButtonText,
  getConfirmButtonText,
  getRejectButtonText,
}: MobileAudioSheetProps) {
  return (
    <div className="sm:hidden bg-card border-t border-border flex-shrink-0 pb-[env(safe-area-inset-bottom)]">
      <audio ref={audioRef} src={audioUrl} className="hidden" />

      {/* Grab handle */}
      <button
        onClick={onToggleExpanded}
        className="w-full flex justify-center py-1.5 touch-manipulation"
      >
        <div className="w-10 h-1 bg-muted-foreground/30 rounded-full" />
      </button>

      {/* Mini player (collapsed) */}
      {!audioSheetExpanded && (
        <div className="px-3 pb-3 space-y-2">
          {/* Progress bar - full width, above controls */}
          <div
            ref={progressBarRef}
            className={`relative bg-muted rounded-full cursor-pointer touch-manipulation transition-all ${isDraggingProgress ? 'h-5' : 'h-2'}`}
            onClick={onProgressClick}
            onTouchStart={onProgressDragStart}
            onTouchMove={onProgressDrag}
            onTouchEnd={onProgressDragEnd}
          >
            <div
              className="absolute top-0 left-0 h-full bg-primary rounded-full pointer-events-none"
              style={{ width: `${(currentTime / (audioDuration || 1)) * 100}%` }}
            />
            <div
              className={`absolute top-1/2 -translate-y-1/2 bg-primary rounded-full shadow-md transition-all pointer-events-none ${isDraggingProgress ? 'w-6 h-6' : 'w-3 h-3'}`}
              style={{ left: `calc(${(currentTime / (audioDuration || 1)) * 100}% - ${isDraggingProgress ? '12px' : '6px'})` }}
            />
          </div>
          {/* Play + time display row */}
          <div className="flex items-center gap-2">
            <button
              onClick={onPlayPause}
              className="p-2 rounded-full bg-primary text-primary-foreground active:scale-95 touch-manipulation flex items-center justify-center transition-all flex-shrink-0"
              aria-label={isPlaying ? 'Pause' : 'Play'}
            >
              {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
            </button>
            <span className="text-xs font-mono text-muted-foreground">{formatTime(currentTime)}</span>
            <span className="text-xs text-muted-foreground mx-auto font-mono">
              {formatTime(adjustedStart)} - {formatTime(adjustedEnd)} ({formatTime(adjustedEnd - adjustedStart)})
            </span>
          </div>
          {/* Time controls - each gets a full row */}
          <BoundaryControls
            variant="mobile-mini"
            selectedAd={selectedAd}
            adjustedStart={adjustedStart}
            adjustedEnd={adjustedEnd}
            startAdjustment={startAdjustment}
            endAdjustment={endAdjustment}
            onStartAdjustmentChange={onStartAdjustmentChange}
            onEndAdjustmentChange={onEndAdjustmentChange}
            triggerHaptic={triggerHaptic}
          />
          {/* Action buttons - full width row */}
          <ActionButtons
            variant="mobile-mini"
            saveStatus={saveStatus}
            onSave={onSave}
            onConfirm={onConfirm}
            onReject={onReject}
            onReset={onReset}
            getSaveButtonText={getSaveButtonText}
            getConfirmButtonText={getConfirmButtonText}
            getRejectButtonText={getRejectButtonText}
          />
        </div>
      )}

      {/* Expanded player */}
      {audioSheetExpanded && (
        <div className="px-4 pb-4 space-y-4">
          {/* Large progress bar */}
          <div
            ref={progressBarRef}
            className={`relative bg-muted rounded-full cursor-pointer touch-manipulation transition-all ${isDraggingProgress ? 'h-6' : 'h-5'}`}
            onClick={onProgressClick}
            onTouchStart={onProgressDragStart}
            onTouchMove={onProgressDrag}
            onTouchEnd={onProgressDragEnd}
          >
            <div
              className="absolute top-0 left-0 h-full bg-primary rounded-full pointer-events-none"
              style={{ width: `${(currentTime / (audioDuration || 1)) * 100}%` }}
            />
            <div
              className={`absolute top-1/2 -translate-y-1/2 bg-primary rounded-full shadow-md transition-all pointer-events-none ${isDraggingProgress ? 'w-7 h-7' : 'w-5 h-5'}`}
              style={{ left: `calc(${(currentTime / (audioDuration || 1)) * 100}% - ${isDraggingProgress ? '14px' : '10px'})` }}
            />
          </div>

          {/* Time display */}
          <div className="flex justify-between text-sm text-muted-foreground font-mono">
            <span>{formatTime(currentTime)}</span>
            <span>{formatTime(audioDuration || 0)}</span>
          </div>

          {/* Large play controls */}
          <div className="flex items-center justify-center gap-4">
            <button onClick={onGoToPreviousAd} className="p-3 rounded-full bg-muted active:bg-accent touch-manipulation min-w-[48px] min-h-[48px] flex items-center justify-center active:scale-95 transition-all">
              <ChevronLeft className="w-6 h-6" />
            </button>
            <button
              onClick={onPlayPause}
              className="p-4 rounded-full bg-primary text-primary-foreground active:scale-95 touch-manipulation min-w-[64px] min-h-[64px] flex items-center justify-center transition-all"
            >
              {isPlaying ? <Pause className="w-8 h-8" /> : <Play className="w-8 h-8" />}
            </button>
            <button onClick={onGoToNextAd} className="p-3 rounded-full bg-muted active:bg-accent touch-manipulation min-w-[48px] min-h-[48px] flex items-center justify-center active:scale-95 transition-all">
              <ChevronRight className="w-6 h-6" />
            </button>
          </div>

          {/* Time controls in expanded mode */}
          <BoundaryControls
            variant="mobile-expanded"
            selectedAd={selectedAd}
            adjustedStart={adjustedStart}
            adjustedEnd={adjustedEnd}
            startAdjustment={startAdjustment}
            endAdjustment={endAdjustment}
            onStartAdjustmentChange={onStartAdjustmentChange}
            onEndAdjustmentChange={onEndAdjustmentChange}
            triggerHaptic={triggerHaptic}
          />

          {/* Action buttons with labels in expanded mode */}
          <ActionButtons
            variant="mobile-expanded"
            saveStatus={saveStatus}
            onSave={onSave}
            onConfirm={onConfirm}
            onReject={onReject}
            onReset={onReset}
            getSaveButtonText={getSaveButtonText}
            getConfirmButtonText={getConfirmButtonText}
            getRejectButtonText={getRejectButtonText}
          />
        </div>
      )}
    </div>
  );
}
