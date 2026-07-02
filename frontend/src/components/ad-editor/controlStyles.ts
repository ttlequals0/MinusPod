// Canonical control styles shared by the audio-editor transport and zoom
// controls so the "Add new ad" and "Mark cue" modals render identically.
// Sourced from AdReviewModal's button recipes.

export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2] as const;

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

// Amber-accented icon button for "play selection", tied visually to the amber
// selection region and the "IN SELECTION" badge so users spot it among the
// transport controls. Same p-1.5 footprint as the ghost icon buttons so it
// does not crowd the row. Amber shifts a stop darker in light mode for contrast.
export const selectionBtn =
  'inline-flex items-center gap-0.5 px-2 py-1.5 rounded transition-colors ' +
  'border border-amber-600/50 text-amber-600 bg-amber-500/10 ' +
  'hover:bg-amber-500/20 hover:border-amber-600 ' +
  'dark:border-amber-500/60 dark:text-amber-500 dark:hover:border-amber-500 ' +
  'focus:outline-hidden focus:ring-2 focus:ring-amber-500 ' +
  'disabled:opacity-40 disabled:cursor-not-allowed';
