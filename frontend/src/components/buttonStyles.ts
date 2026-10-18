// Shared button class recipes -- the single source for the app's standard
// button variants. Layout/sizing utilities (px-*, w-full, text-sm, rounded*)
// and per-site state modifiers (disabled:opacity-50, transition-colors) stay
// inline at call sites next to the recipe.
export const btnPrimary = 'bg-primary text-primary-foreground hover:bg-primary/90';
export const btnSecondary = 'bg-secondary text-secondary-foreground hover:bg-secondary/80';
// Outline-style buttons render with the secondary recipe since 2.60.0 (#534):
// the old `border border-border hover:bg-accent` recipe was nearly invisible
// in dark mode. Kept as a separate name so call sites keep their intent.
export const btnOutline = btnSecondary;
export const btnGhost = 'text-muted-foreground hover:text-foreground hover:bg-accent';
export const btnDestructive = 'bg-destructive text-destructive-foreground hover:bg-destructive/90';

// index.css enforces the coarse-pointer height floor at every width; width is a default a recipe may override.
export const touchTarget =
  'touch-target inline-flex items-center justify-center min-h-11 min-w-11 sm:min-h-0 sm:min-w-0';
// Icon-only header actions beside a heading: a 36 px square on a phone, text button from sm.
export const touchCompact =
  'touch-compact inline-flex items-center justify-center h-9 w-9 max-sm:min-w-9 sm:h-auto sm:w-auto';
// For a row that must stay on one line on a phone: full height, shared width below sm.
export const touchTargetRow =
  'inline-flex items-center justify-center min-h-11 sm:min-h-0 max-sm:min-w-0';
