import { ReactNode, type SyntheticEvent, useEffect, useId, useRef } from 'react';
import { btnDestructive, btnOutline, btnPrimary } from './buttonStyles';
import { focusRing } from './fieldStyles';

// Shared modal recipes. cueScanStyles re-exports these for the cue feature's
// recipe-based modals; everything else renders through <Modal>.
export const modalBackdrop = 'modal-backdrop fixed inset-0 z-60 flex items-center justify-center bg-black/50 p-4';
// Modal's own panel omits text-foreground (see panelClassName comment below);
// the exported recipe keeps it for standalone use.
const modalPanelBase = 'bg-card rounded-lg border border-border shadow-xl';
export const modalPanel = modalPanelBase + ' text-foreground';

// Close-on-Escape for modals that opt in (also used standalone by the cue
// feature's recipe-based modals via the cueScanStyles re-export).
export function isTopDialog(panel: HTMLElement | null) {
  const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]');
  return panel === dialogs[dialogs.length - 1];
}

export function useEscape(onClose: () => void, enabled = true, panelRef?: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (panelRef?.current && (!isTopDialog(panelRef.current)
        || document.querySelector(`[data-modal-owner="${panelRef.current.dataset.dialogId}"]`))) return;
      e.preventDefault();
      onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, enabled, panelRef]);
}

export const FOCUSABLE = 'summary,a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

let dialogTrigger: HTMLElement | null = null;

// WebKit does not focus buttons on pointer activation.
export function captureDialogTrigger(event: SyntheticEvent) {
  dialogTrigger = event.type === 'click' && event.target instanceof Element
    ? event.target.closest<HTMLElement>(FOCUSABLE) : null;
}

const inertLocks = new Map<HTMLElement, { count: number; previous: boolean }>();
let scrollLocks = 0;
let previousOverflow = '';

// Keeps Tab inside the dialog and returns focus to the trigger on close, so a
// keyboard user is never dropped onto the page behind an open dialog.
export function useFocusTrap(panelRef: React.RefObject<HTMLElement | null>, focusPanel = false) {
  const id = useId();
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    if (!returnFocus.current) {
      returnFocus.current = dialogTrigger?.isConnected && !panel.contains(dialogTrigger)
        ? dialogTrigger : document.activeElement as HTMLElement | null;
      dialogTrigger = null;
    }
    const previous = returnFocus.current;
    panel.dataset.dialogId = id;
    if (scrollLocks++ === 0) previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const siblings: HTMLElement[] = [];
    const dialogs = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
    const topDialog = dialogs[dialogs.length - 1] ?? null;
    let branch: HTMLElement = panel;
    while (branch.parentElement) {
      for (const sibling of Array.from(branch.parentElement.children)) {
        if (sibling instanceof HTMLElement && sibling !== branch && !sibling.contains(topDialog)) {
          const lock = inertLocks.get(sibling) ?? { count: 0, previous: sibling.inert };
          lock.count++;
          inertLocks.set(sibling, lock);
          siblings.push(sibling);
          sibling.inert = true;
        }
      }
      branch = branch.parentElement;
      if (branch === document.body) break;
    }

    const portals = () => Array.from(document.querySelectorAll<HTMLElement>(`[data-modal-owner="${id}"]`));
    const targets = () => [panel, ...portals()].flatMap(root => Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)))
      .filter(el => el.tabIndex >= 0 && !el.closest('[inert]') && (el.offsetParent !== null || el === document.activeElement));

    (focusPanel ? panel : targets()[0] ?? panel).focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !isTopDialog(panel)) return;
      const items = targets();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const index = items.indexOf(document.activeElement as HTMLElement);
      const next = e.shiftKey ? (index <= 0 ? items.length - 1 : index - 1) : (index + 1) % items.length;
      e.preventDefault();
      items[next].focus();
    };

    const onFocus = (event: FocusEvent) => {
      const target = event.target as Node;
      if (isTopDialog(panel) && !panel.contains(target) && !portals().some(root => root.contains(target))) (targets()[0] ?? panel).focus();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('focusin', onFocus);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('focusin', onFocus);
      siblings.forEach(sibling => {
        const lock = inertLocks.get(sibling)!;
        if (--lock.count === 0) {
          sibling.inert = lock.previous;
          inertLocks.delete(sibling);
        }
      });
      if (--scrollLocks === 0) document.body.style.overflow = previousOverflow;
      queueMicrotask(() => {
        if (!panel.isConnected && previous?.isConnected && !previous.closest('[inert]')) previous.focus?.();
      });
    };
  }, [panelRef, id, focusPanel]);
}

type ModalProps = ({ ariaLabelledBy: string; ariaLabel?: never } | { ariaLabel: string; ariaLabelledBy?: never }) & {
  // Called by Escape / click-outside when those are enabled.
  onClose: () => void;
  // Escape closes by default; pass false for a dialog that must not.
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
  onClose, closeOnEscape = true, closeOnBackdrop = false, ariaLabelledBy, ariaLabel,
  panelClassName = '', children,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  useEscape(onClose, closeOnEscape, panelRef);
  useFocusTrap(panelRef);

  return (
    <div className={modalBackdrop} onClick={closeOnBackdrop ? onClose : undefined}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={ariaLabelledBy}
        aria-label={ariaLabel}
        tabIndex={-1}
        className={`${modalPanelBase} ${focusRing} ${panelClassName}`}
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
  const titleId = useId();
  return (
    <Modal onClose={onCancel} closeOnEscape={!pending} ariaLabelledBy={titleId} panelClassName="max-w-md w-full">
      <div className="p-4 border-b border-border">
        <h2 id={titleId} className="text-lg font-semibold text-foreground">{title}</h2>
      </div>
      <div className="p-4 space-y-3 text-sm text-foreground">{children}</div>
      <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
        <button
          onClick={onCancel}
          disabled={pending}
          className={`px-3 py-1.5 text-sm rounded ${btnOutline} transition-colors ${focusRing}`}
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          disabled={pending}
          className={`px-3 py-1.5 text-sm rounded ${destructive ? btnDestructive : btnPrimary} disabled:opacity-50 transition-colors ${focusRing}`}
        >
          {pending ? busyLabel : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
