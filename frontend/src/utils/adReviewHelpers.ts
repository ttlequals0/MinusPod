// Shared, state-free helpers extracted from AdReviewModal. Keeping them
// here keeps the modal focused on composition and lets us unit-test the
// pure pieces without mounting React.

export const PLAY_WHILE_DRAG_KEY = 'minuspod.adInbox.playWhileDragging';

// Parse user-entered timestamp text. Accepts either `MM:SS[.s]` (or
// `H:MM:SS[.s]`) or a raw seconds value like `139.4`. Returns null on
// any invalid input so callers can keep the prior boundary.
export function parseTimeInput(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  if (t.includes(':')) {
    const parts = t.split(':');
    if (parts.length < 2 || parts.length > 3) return null;
    const nums = parts.map(Number);
    if (nums.some((n) => !Number.isFinite(n) || n < 0)) return null;
    if (parts.length === 2) return nums[0] * 60 + nums[1];
    return nums[0] * 3600 + nums[1] * 60 + nums[2];
  }
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return '0:00';
  const sign = seconds < 0 ? '-' : '';
  const total = Math.round(Math.abs(seconds) * 10) / 10;
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${sign}${m}:${s.toFixed(1).padStart(4, '0')}`;
}

// Canonical waveform color for both audio-editor modals (cue + ad edit). Both
// render their own amber playhead overlay (cursorColor transparent), so
// wavesurfer's built-in unplayed/played split is unused -- left to its default
// it would only show a muted grey waveform at rest. Return the theme primary
// for BOTH the bar and the progress fill so the two editors read as one vivid,
// identically-themed waveform. Single source: changing this shifts both.
export function getThemeWaveformColors(): { waveColor: string; progressColor: string } {
  if (typeof window === 'undefined') {
    return { waveColor: '#22d3ee', progressColor: '#22d3ee' };
  }
  const primary = getComputedStyle(document.documentElement)
    .getPropertyValue('--primary').trim();
  const color = primary ? `hsl(${primary})` : '#22d3ee';
  return { waveColor: color, progressColor: color };
}

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
