import { focusRing } from './fieldStyles';

// Compose `${btnBase} ${btnPrimary}`: the base carries size, radius, and focus; the variant carries colour only.
// Call sites add only layout classes (w-full, gap overrides). btnLg is for page-level form actions.
const btnShape = 'inline-flex items-center justify-center gap-1.5 text-sm rounded whitespace-nowrap transition-colors disabled:opacity-50 disabled:pointer-events-none';
export const btnBase = `${btnShape} px-3 py-1.5 ${focusRing}`;
export const btnLg = `${btnShape} px-4 py-2 ${focusRing}`;
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
// Destructive action that is not the primary one in its group (Delete beside Edit).
export const btnDestructiveOutline = 'border border-destructive/40 text-destructive hover:bg-destructive/10';
