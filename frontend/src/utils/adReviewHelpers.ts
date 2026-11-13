import type { KeyboardEvent } from 'react';

// Shared, state-free helpers extracted from AdReviewModal. Keeping them
// here keeps the modal focused on composition and lets us unit-test the
// pure pieces without mounting React.

const PLAY_WHILE_DRAG_KEY = 'minuspod.adInbox.playWhileDragging';

const INT_PART = /^\d+$/;
const LAST_PART = /^\d+(\.\d+)?$/;

// Parse user-entered timestamp text: `MM:SS[.s]`, `H:MM:SS[.s]` or raw seconds like `139.4`. Returns null on
// invalid input so callers keep the prior boundary. Number()'s leniency (hex, junk, exponents) is avoided by
// matching each part against a plain-digits pattern.
export function parseTimeInput(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  const parts = t.split(':');
  if (parts.length > 3) return null;
  for (let i = 0; i < parts.length; i++) {
    const re = i === parts.length - 1 ? LAST_PART : INT_PART;
    if (!re.test(parts[i])) return null;
  }
  const nums = parts.map(Number);
  if (nums.slice(1).some((n) => n >= 60)) return null;
  if (nums.length === 1) return nums[0];
  if (nums.length === 2) return nums[0] * 60 + nums[1];
  return nums[0] * 3600 + nums[1] * 60 + nums[2];
}

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return '0:00';
  const sign = seconds < 0 ? '-' : '';
  const total = Math.round(Math.abs(seconds) * 10) / 10;
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${sign}${m}:${s.toFixed(1).padStart(4, '0')}`;
}

// Canonical waveform color for both audio-editor modals. Both draw their own amber playhead overlay
// (cursorColor transparent), so wavesurfer's unplayed/played split is unused and its default would show a
// muted grey waveform at rest. Returns the theme primary for both bar and progress fill so the editors read
// as one vivid waveform; changing this shifts both.
// Slate light --primary, for the window-less and unresolved-variable paths.
const DEFAULT_WAVE = 'hsl(194 66% 45%)';

export function getThemeWaveformColors(): { waveColor: string; progressColor: string } {
  if (typeof window === 'undefined') {
    return { waveColor: DEFAULT_WAVE, progressColor: DEFAULT_WAVE };
  }
  const primary = getComputedStyle(document.documentElement)
    .getPropertyValue('--primary').trim();
  const color = primary ? `hsl(${primary})` : DEFAULT_WAVE;
  return { waveColor: color, progressColor: color };
}

// Shared commit for the timestamp inputs of both audio-editor modals. Clamps only to [0, maxSeconds]; Start <
// End is enforced at Save so editing one field does not stomp the other on blur. Invalid input reverts to the
// current boundary. `onCommitted` runs only on a successful commit (e.g. AdReviewModal recenters its window).
export function commitTimeInput(
  input: string,
  fallback: number,
  maxSeconds: number,
  setBoundary: (v: number) => void,
  setInput: (s: string) => void,
  onCommitted?: (v: number) => void,
): void {
  const parsed = parseTimeInput(input);
  if (parsed === null) {
    setInput(formatTime(fallback));
    return;
  }
  const clamped = Math.max(0, Math.min(parsed, maxSeconds));
  setBoundary(clamped);
  setInput(formatTime(clamped));
  onCommitted?.(clamped);
}

// Enter commits (via blur), Escape reverts to the current boundary.
export const timeInputKeyDown =
  (fallback: number, setInput: (s: string) => void) =>
  (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      (e.target as HTMLInputElement).blur();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setInput(formatTime(fallback));
      (e.target as HTMLInputElement).blur();
    }
  };

export function loadPlayWhileDragging(): boolean {
  try {
    return localStorage.getItem(PLAY_WHILE_DRAG_KEY) === '1';
  } catch {
    return false;
  }
}

export function savePlayWhileDragging(v: boolean) {
  try {
    localStorage.setItem(PLAY_WHILE_DRAG_KEY, v ? '1' : '0');
  } catch {
    /* private mode etc */
  }
}
