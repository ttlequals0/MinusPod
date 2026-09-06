// Canonical control styles shared by the audio-editor transport and zoom
// controls so the "Add new ad" and "Mark cue" modals render identically.
// Sourced from AdReviewModal's button recipes.

export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] as const;

// Deliberately divergent from components/buttonStyles.ts: the compact editor
// controls use a bordered ghost recipe and a ring-hover primary on purpose.
export const ghostBtn =
  'border border-border text-foreground bg-card transition-colors ' +
  'hover:bg-accent hover:text-accent-foreground hover:border-foreground/30 ' +
  'disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-card ' +
  'disabled:hover:text-foreground disabled:hover:border-border';

export const primaryBtn =
  'bg-primary text-primary-foreground transition-all ' +
  'hover:bg-primary hover:ring-2 hover:ring-primary hover:ring-offset-2 hover:ring-offset-card ' +
  'disabled:opacity-50 disabled:cursor-not-allowed';

export const ctrlBtn = `px-2 py-1.5 rounded ${ghostBtn} text-sm`;

// "Set START/END at playhead". Once the waveform is zoomed the pins sit off
// screen, and on a phone there is no wheel to zoom back out, so these are the
// only way to place a boundary; 44px keeps them on the tap-target floor.
// Deliberately not built on ghostBtn: its text-foreground competes with the
// caller's text-success/text-destructive, and which one wins is down to
// Tailwind's output order (END rendered grey while START rendered green).
export const edgeBtn =
  'flex-1 sm:flex-none min-h-[44px] inline-flex items-center justify-center ' +
  'whitespace-nowrap px-2 py-1.5 rounded text-sm border border-border bg-card ' +
  'transition-colors hover:bg-accent hover:border-foreground/30 ' +
  'disabled:opacity-40 disabled:cursor-not-allowed';

// Amber "play selection" button, matched to the amber selection region/badge.
// Wider than the ghost icon buttons (px-2) to fit the bracketed [play] glyphs.
export const selectionBtn =
  'inline-flex items-center gap-0.5 px-2 py-1.5 rounded transition-colors ' +
  'border border-warning/50 text-warning bg-warning/10 ' +
  'hover:bg-warning/20 hover:border-warning ' +
  'dark:border-warning/60 dark:text-warning dark:hover:border-warning ' +
  'focus:outline-hidden focus:ring-2 focus:ring-warning ' +
  'disabled:opacity-40 disabled:cursor-not-allowed';
