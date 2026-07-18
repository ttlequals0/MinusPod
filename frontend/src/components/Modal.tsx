import { ReactNode, useEffect } from 'react';
import { btnDestructive, btnOutline, btnPrimary } from './buttonStyles';

// Shared modal recipes. cueScanStyles re-exports these for the cue feature's
// recipe-based modals; everything else renders through <Modal>.
export const modalBackdrop = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4';
// Modal's own panel omits text-foreground (see panelClassName comment below);
// the exported recipe keeps it for standalone use.
const modalPanelBase = 'bg-card rounded-lg border border-border shadow-xl';
export const modalPanel = modalPanelBase + ' text-foreground';

// Close-on-Escape for modals that opt in (also used standalone by the cue
// feature's recipe-based modals via the cueScanStyles re-export).
export function useEscape(onClose: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, enabled]);
}

interface ModalProps {
  // Called by Escape / click-outside when those are enabled.
  onClose: () => void;
  // Both default off: most dialogs close only via their explicit buttons,
  // and each migrated dialog must keep its pre-existing behavior.
  closeOnEscape?: boolean;
  closeOnBackdrop?: boolean;
  // Sizing/layout plus any text-color override. The panel deliberately sets
  // no text color of its own: dialogs historically inherit the body
  // foreground, and card-foreground differs from foreground in dark mode,
  // so dialogs that used text-card-foreground pass it here.
  panelClassName?: string;
  children: ReactNode;
}

export function Modal({
  onClose, closeOnEscape = false, closeOnBackdrop = false, panelClassName = '', children,
}: ModalProps) {
  useEscape(onClose, closeOnEscape);

  return (
    <div className={modalBackdrop} onClick={closeOnBackdrop ? onClose : undefined}>
      <div
        className={`${modalPanelBase} ${panelClassName}`}
        onClick={closeOnBackdrop ? (e) => e.stopPropagation() : undefined}
      >
        {children}
      </div>
    </div>
  );
}

interface ConfirmModalProps {
  title: string;
  children?: ReactNode;
  confirmLabel?: string;
  busyLabel?: string;
  destructive?: boolean;
  pending?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmModal({
  title, children, confirmLabel = 'Delete', busyLabel = 'Deleting...',
  destructive = true, pending = false, onCancel, onConfirm,
}: ConfirmModalProps) {
  return (
    <Modal onClose={onCancel} panelClassName="max-w-md w-full">
      <div className="p-4 border-b border-border">
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      </div>
      <div className="p-4 space-y-3 text-sm text-foreground">{children}</div>
      <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
        <button
          onClick={onCancel}
          className={`px-3 py-1.5 text-sm rounded ${btnOutline} transition-colors`}
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          disabled={pending}
          className={`px-3 py-1.5 text-sm rounded ${destructive ? btnDestructive : btnPrimary} disabled:opacity-50 transition-colors`}
        >
          {pending ? busyLabel : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
