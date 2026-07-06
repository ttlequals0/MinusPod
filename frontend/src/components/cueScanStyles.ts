import { useEffect } from 'react';

// Design-system recipes shared by CueTemplatesPanel and its extracted scan
// modals/panels (match the app's confirm/edit modals and form controls;
// theme-aware in dark mode).
export const ghostBtn = 'border border-border hover:bg-accent transition-colors';
export const primaryBtn = 'bg-primary text-primary-foreground hover:bg-primary/90 transition-colors';
export const fieldCls = 'rounded-lg border border-input bg-background text-foreground focus:outline-hidden focus:ring-2 focus:ring-ring';
export const modalBackdrop = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4';
export const modalPanel = 'bg-card text-foreground rounded-lg border border-border shadow-xl';

// Close-on-Escape for the lightweight cue modals.
export function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
}
