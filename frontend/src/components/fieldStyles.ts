// Shared field recipes, the counterpart to buttonStyles.ts. Import these so
// focus treatment and field chrome stay identical across every screen.

// The design guide's ring-2 ring-ring rule, on focus-visible so a mouse click
// doesn't leave a ring behind.
export const focusRing = 'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring';

// Selects are the one field on a secondary fill at 4px radius.
export const selectBase = `px-3 py-2 rounded bg-secondary text-secondary-foreground border border-border text-sm ${focusRing}`;
