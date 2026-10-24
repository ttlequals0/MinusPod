// Shared field recipes, the counterpart to buttonStyles.ts. Import these so
// focus treatment and field chrome stay identical across every screen.

// The design guide's ring-2 ring-ring rule, on focus-visible so a mouse click
// doesn't leave a ring behind.
export const focusRing = 'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring';

// One chrome and one height for every text field, select, and textarea.
const fieldChrome = `px-3 py-2 rounded border border-input bg-background text-foreground text-sm placeholder:text-muted-foreground ${focusRing}`;
export const inputBase = `min-h-10 ${fieldChrome}`;
export const selectBase = inputBase;
// Textareas size by rows, so no min-height.
export const textareaBase = fieldChrome;
export const labelBase = 'block text-sm font-medium text-foreground mb-1.5';
export const filterGrid = 'grid grid-cols-1 min-[375px]:grid-cols-2 gap-4';
export const filterLabel = 'mb-1 block text-sm text-muted-foreground';
export const filterControl = 'min-h-11 min-w-0 w-full';
// Native file input styled as a secondary button.
export const fileInputBase = `block w-full text-sm text-muted-foreground file:mr-3 file:px-3 file:py-1.5 file:rounded file:border-0 file:text-sm bg-secondary text-secondary-foreground hover:bg-secondary/80 file:transition-colors ${focusRing}`;
