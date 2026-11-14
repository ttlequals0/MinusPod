import { focusRing } from './fieldStyles';

// Compose `${btnBase} ${btnPrimary}`: the base carries size, radius, and focus; the variant carries colour only.
// Call sites add only layout classes (w-full, gap overrides). btnLg is for page-level form actions.
const btnShape = 'inline-flex items-center justify-center gap-1.5 text-sm rounded whitespace-nowrap transition-colors disabled:opacity-50 disabled:pointer-events-none';
// Shared with the toolbar SegmentedToggle so it stays the height of the buttons beside it.
export const btnPadY = 'py-1.5';
export const btnBase = `${btnShape} px-3 ${btnPadY} ${focusRing}`;
export const btnLg = `${btnShape} px-4 py-2 ${focusRing}`;
export const btnPrimary = 'bg-primary text-primary-foreground hover:bg-primary/90';
export const btnSecondary = 'bg-secondary text-secondary-foreground hover:bg-secondary/80';
// Outline-style buttons render with the secondary recipe since 2.60.0 (#534):
// the old `border border-border hover:bg-accent` recipe was nearly invisible
// in dark mode. Kept as a separate name so call sites keep their intent.
export const btnOutline = btnSecondary;
export const btnGhost = 'text-muted-foreground hover:text-foreground hover:bg-accent';
export const btnDestructive = 'bg-destructive text-destructive-foreground hover:bg-destructive/90';

// 44 x 44 px on coarse pointers at every width (index.css: height floor, base-layer width for .touch-target)
// and below sm on any pointer; from sm on a fine pointer it has no floor. Never add a min-w-0 from sm.
export const touchTarget =
  'touch-target inline-flex items-center justify-center min-h-11 max-sm:min-w-11 sm:min-h-0';
// Icon-only header actions beside a heading: a 36 px square on a phone, text button from sm.
export const touchCompact =
  'touch-compact inline-flex items-center justify-center h-9 w-9 max-sm:min-w-9 sm:h-auto sm:w-auto';
// For a row that must stay on one line on a phone: full height, shared width below sm.
export const touchTargetRow =
  'inline-flex items-center justify-center min-h-11 sm:min-h-0 max-sm:min-w-0';
// The outline is an inset shadow, not a border, so these stay the height of the fill buttons beside them.
// Accent action that should not dominate its surface (Refresh on feed cards, rows and page).
// 80 percent keeps the border above the 3:1 non-text floor on every theme.
export const btnPrimaryOutline = 'shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.8)] text-primary hover:bg-primary/10';
// Destructive action that is not the primary one in its group (Delete beside Edit).
export const btnDestructiveOutline = 'shadow-[inset_0_0_0_1px_hsl(var(--destructive)/0.8)] text-destructive hover:bg-destructive/10';
