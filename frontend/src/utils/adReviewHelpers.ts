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
  const total = Math.abs(seconds);
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${sign}${m}:${s.toFixed(1).padStart(4, '0')}`;
}

// Read the current theme's wave + progress colors from the CSS vars
// that already drive the rest of the UI, so the waveform shifts with
// the active theme instead of staying fixed at slate/cyan.
export function getThemeWaveformColors(): { waveColor: string; progressColor: string } {
  if (typeof window === 'undefined') {
    return { waveColor: '#64748b', progressColor: '#22d3ee' };
  }
  const cs = getComputedStyle(document.documentElement);
  const muted = cs.getPropertyValue('--muted-foreground').trim();
  const primary = cs.getPropertyValue('--primary').trim();
  return {
    waveColor: muted ? `hsl(${muted})` : '#64748b',
    progressColor: primary ? `hsl(${primary})` : '#22d3ee',
  };
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
