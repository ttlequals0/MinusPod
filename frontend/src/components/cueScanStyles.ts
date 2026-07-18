import { btnOutline, btnPrimary } from './buttonStyles';

// The modal backdrop/panel recipes and the Escape hook now live in the
// shared Modal module; re-exported here for the cue feature's existing
// recipe-based consumers.
export { modalBackdrop, modalPanel, useEscape } from './Modal';

// Design-system recipes shared by CueTemplatesPanel and its extracted scan
// modals/panels (match the app's confirm/edit modals and form controls;
// theme-aware in dark mode).
// ghostBtn used the pre-2.60.0 `border border-border hover:bg-accent` recipe,
// which was nearly invisible in dark mode (#534); it now renders as btnOutline.
export const ghostBtn = `${btnOutline} transition-colors`;
export const primaryBtn = `${btnPrimary} transition-colors`;
export const fieldCls = 'rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring';
