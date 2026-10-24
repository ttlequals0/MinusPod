import { inputBase } from './fieldStyles';
import { btnBase, btnOutline, btnPrimary } from './buttonStyles';

// The modal backdrop/panel recipes and the Escape hook now live in the
// shared Modal module; re-exported here for the cue feature's existing
// recipe-based consumers.
export { modalBackdrop, modalPanel, useEscape } from './Modal';

// Design-system recipes shared by CueTemplatesPanel and its extracted scan
// modals/panels (match the app's confirm/edit modals and form controls;
// theme-aware in dark mode).
export const ghostBtn = `${btnBase} ${btnOutline}`;
export const primaryBtn = `${btnBase} ${btnPrimary}`;
export const fieldCls = inputBase;
