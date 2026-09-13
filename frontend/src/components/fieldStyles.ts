// Shared field recipes, the counterpart to buttonStyles.ts. Import these so
// focus treatment and field chrome stay identical across every screen.

// The design guide's ring-2 ring-ring rule, on focus-visible so a mouse click
// doesn't leave a ring behind.
export const focusRing = 'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring';

// Selects are the one field on a secondary fill at 4px radius.
export const selectBase = `px-3 py-2 rounded bg-secondary text-secondary-foreground border border-border text-sm ${focusRing}`;

// Single-line text input: the same chrome AIModelsSection and the settings
// User-Agent fields were each spelling out inline.
export const inputBase = `px-3 py-2 rounded-lg border border-input bg-background text-foreground text-sm ${focusRing}`;
// Native file input styled as a secondary button.
export const fileInputBase = `block w-full text-sm text-muted-foreground file:mr-3 file:px-3 file:py-1.5 file:rounded file:border-0 file:text-sm bg-secondary text-secondary-foreground hover:bg-secondary/80 file:transition-colors ${focusRing}`;
