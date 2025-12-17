import { useEffect, useCallback } from 'react';

interface KeyboardHandlers {
  onPlayPause: () => void;
  onNudgeEndForward: () => void;
  onNudgeEndBackward: () => void;
  onNudgeStartForward: () => void;
  onNudgeStartBackward: () => void;
  onSave: () => void;
  onReset: () => void;
  onConfirm?: () => void;
  onReject?: () => void;
}

interface UseTranscriptKeyboardOptions {
  enabled?: boolean;
  nudgeAmount?: number; // seconds
}

/**
 * Hook for keyboard shortcuts in transcript editor
 *
 * Shortcuts:
 * - Space: Play/pause audio
 * - J: Nudge end boundary backward
 * - K: Nudge end boundary forward
 * - Shift+J: Nudge start boundary backward
 * - Shift+K: Nudge start boundary forward
 * - Enter: Save changes
 * - Escape: Reset to original
 * - C: Confirm ad (mark as correct)
 * - X: Reject ad (mark as false positive)
 */
export function useTranscriptKeyboard(
  handlers: KeyboardHandlers,
  options: UseTranscriptKeyboardOptions = {}
) {
  const { enabled = true } = options;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      // Don't handle if typing in an input
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      // Don't handle if modifier keys (except shift) are pressed
      if (e.ctrlKey || e.altKey || e.metaKey) {
        return;
      }

      switch (e.key) {
        case ' ':
          e.preventDefault();
          handlers.onPlayPause();
          break;

        case 'j':
        case 'J':
          e.preventDefault();
          if (e.shiftKey) {
            handlers.onNudgeStartBackward();
          } else {
            handlers.onNudgeEndBackward();
          }
          break;

        case 'k':
        case 'K':
          e.preventDefault();
          if (e.shiftKey) {
            handlers.onNudgeStartForward();
          } else {
            handlers.onNudgeEndForward();
          }
          break;

        case 'Enter':
          e.preventDefault();
          handlers.onSave();
          break;

        case 'Escape':
          e.preventDefault();
          handlers.onReset();
          break;

        case 'c':
        case 'C':
          e.preventDefault();
          handlers.onConfirm?.();
          break;

        case 'x':
        case 'X':
          e.preventDefault();
          handlers.onReject?.();
          break;
      }
    },
    [handlers]
  );

  useEffect(() => {
    if (!enabled) return;

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [enabled, handleKeyDown]);
}

export default useTranscriptKeyboard;
