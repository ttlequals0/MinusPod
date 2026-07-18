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
