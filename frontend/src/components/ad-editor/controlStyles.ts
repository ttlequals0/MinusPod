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
