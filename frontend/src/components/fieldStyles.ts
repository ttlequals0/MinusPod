// Shared field recipes, the counterpart to buttonStyles.ts. Import these so
// focus treatment and field chrome stay identical across every screen.

// The design guide's focus rule (ring-2 ring-ring). focus-visible rather than
// focus so a mouse click doesn't leave a ring behind; browsers still match it
// for keyboard focus and for text fields.
export const focusRing = 'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring';

// Text/number fields: background fill, input border, 8px radius.
export const inputBase = `px-3 py-2 rounded-lg border border-input bg-background text-foreground ${focusRing}`;

// Selects are the one field on a secondary fill at 4px radius.
export const selectBase = `px-3 py-2 rounded bg-secondary text-secondary-foreground border border-border text-sm ${focusRing}`;
