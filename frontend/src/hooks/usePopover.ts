import {
  useCallback, useEffect, useId, useLayoutEffect, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent, type RefObject,
} from 'react';
import { FOCUSABLE } from '../components/Modal';
import { useOutsideClick } from './useOutsideClick';

// Below Tailwind's sm breakpoint a popover is centered on the screen under the
// trigger's row: a row that wraps on a phone leaves no side with room.
const PHONE_MAX_WIDTH_PX = 640;
const GAP_PX = 4;
const VIEWPORT_MARGIN_PX = 8;
// Floor for the scroll area when neither side of the anchor has real room.
const MIN_HEIGHT_PX = 96;

export type PopoverAlign = 'left' | 'right' | 'auto';
export type PopoverCloseReason = 'escape' | 'outside' | 'anchor-hidden';

export interface PopoverOptions {
  onClose: (reason: PopoverCloseReason) => void;
  anchorRef: RefObject<HTMLElement | null>;
  /** Which panel edge lines up with the anchor. `auto` flips to the edge that fits. */
  align?: PopoverAlign;
}

interface Placement {
  top?: number;
  bottom?: number;
  left?: number;
  right?: number;
  maxHeight: number;
  centered: boolean;
}

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max));

const unchanged = (a: Placement | null, b: Placement) =>
  !!a && a.top === b.top && a.bottom === b.bottom && a.left === b.left && a.right === b.right
  && a.maxHeight === b.maxHeight && a.centered === b.centered;

// A rect past a viewport edge on either axis means the anchor scrolled away; no
// client rects at all means it is detached or display:none.
const isAnchorHidden = (a: HTMLElement, r: DOMRect, viewportWidth: number, viewportHeight: number) =>
  !a.isConnected || a.getClientRects().length === 0
  || r.bottom < 0 || r.top > viewportHeight || r.right < 0 || r.left > viewportWidth;

// Fixed bars sit above the popover's layer, so the strip each one covers is not
// room the panel can use. They mark themselves with data-viewport-inset.
function chromeInset(edge: 'top' | 'bottom'): number {
  const bars = document.querySelectorAll<HTMLElement>(`[data-viewport-inset="${edge}"]`);
  return Array.from(bars).reduce((total, bar) => total + bar.getBoundingClientRect().height, 0);
}

// Horizontal insets are measured against documentElement, not window.innerWidth:
// a fixed box's containing block excludes the scrollbar that innerWidth counts.
// Returns null once the anchor is out of view.
function place(anchor: HTMLElement, panel: HTMLElement, align: PopoverAlign): Placement | null {
  const rect = anchor.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;
  if (isAnchorHidden(anchor, rect, viewportWidth, viewportHeight)) return null;

  const usableTop = chromeInset('top') + VIEWPORT_MARGIN_PX;
  const usableBottom = viewportHeight - chromeInset('bottom') - VIEWPORT_MARGIN_PX;
  const spaceBelow = usableBottom - rect.bottom - GAP_PX;
  const spaceAbove = rect.top - GAP_PX - usableTop;
  // scrollHeight is the content height, so the maxHeight written below never
  // feeds back into the fit test and flips the panel on the next measure.
  const above = panel.scrollHeight > spaceBelow && spaceAbove > spaceBelow;
  const height = Math.max(Math.round(above ? spaceAbove : spaceBelow), MIN_HEIGHT_PX);

  // The floor can make the panel taller than its side has room for, so the inset
  // is clamped to keep its far edge inside: it slides over the anchor, not off.
  const common = {
    ...(above
      ? {
        bottom: clamp(
          Math.round(viewportHeight - rect.top + GAP_PX),
          viewportHeight - usableBottom,
          viewportHeight - usableTop - height,
        ),
      }
      : {
        top: clamp(Math.round(rect.bottom + GAP_PX), usableTop, usableBottom - height),
      }),
    maxHeight: height,
  };
  // The phone test reads window.innerWidth, the box Tailwind's sm: breakpoint sees.
  if (window.innerWidth < PHONE_MAX_WIDTH_PX) return { ...common, centered: true };

  const span = panel.offsetWidth;
  const rightEdgeFits = rect.right - span >= VIEWPORT_MARGIN_PX;
  const side = align === 'auto' ? (rightEdgeFits ? 'right' : 'left') : align;
  const limit = viewportWidth - VIEWPORT_MARGIN_PX - span;
  const inset = side === 'left'
    ? { left: clamp(Math.round(rect.left), VIEWPORT_MARGIN_PX, limit) }
    : { right: clamp(Math.round(viewportWidth - rect.right), VIEWPORT_MARGIN_PX, limit) };
  return { ...common, ...inset, centered: false };
}

/** Fixed placement that follows the anchor, plus the Escape, outside-click and
 *  focus-loss dismissal. Mounts with the open panel, so a closed one holds none. */
export function usePopover({ onClose, anchorRef, align = 'auto' }: PopoverOptions) {
  const [placement, setPlacement] = useState<Placement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  // Latest callback and alignment, so a re-render never re-attaches the listeners
  // below. Layout phase, so the measure effect sees the alignment it renders with.
  const latest = useRef({ onClose, align });
  useLayoutEffect(() => {
    latest.current.onClose = onClose;
    latest.current.align = align;
  });

  const measure = useCallback(() => {
    const anchor = anchorRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;
    const next = place(anchor, panel, latest.current.align);
    if (!next) latest.current.onClose('anchor-hidden');
    else setPlacement((prev) => (unchanged(prev, next) ? prev : next));
  }, [anchorRef]);

  // Measuring from the ref callback runs before the first paint, so the panel
  // never shows up at an unplaced position.
  const attachPanel = useCallback((node: HTMLDivElement | null) => {
    panelRef.current = node;
    if (node) measure();
  }, [measure]);

  // Both call sites nest the panel inside the anchor, so the panel ref attaches
  // first and that measure finds no anchor. Also re-places on an align change.
  useLayoutEffect(() => { measure(); }, [align, measure]);

  useEffect(() => {
    // Scroll is captured because the anchor's scroller may be any ancestor. The
    // observer sees its own boxes and the body resize, which catches a toolbar row
    // that wraps, but never a transform; a frame still runs at most one measure.
    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => { frame = 0; measure(); });
    };
    window.addEventListener('scroll', schedule, { capture: true, passive: true });
    window.addEventListener('resize', schedule);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    for (const el of [anchorRef.current, panelRef.current, document.body]) {
      if (el) observer?.observe(el);
    }
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
      observer?.disconnect();
    };
  }, [measure, anchorRef]);

  // Not Modal's useEscape: that one listens on window, and a popover needs the
  // key stopped at document so it never reaches a window listener behind it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      latest.current.onClose('escape');
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // A focus trap opening over the menu (a modal, the command palette) takes focus
  // without a click; closing then leaves no stranded panel behind the new layer.
  useEffect(() => {
    const onFocusIn = (e: FocusEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (anchorRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      latest.current.onClose('outside');
    };
    document.addEventListener('focusin', onFocusIn);
    return () => document.removeEventListener('focusin', onFocusIn);
  }, [anchorRef]);

  useOutsideClick([anchorRef, panelRef], true, () => latest.current.onClose('outside'));

  const { centered = false, ...style } = placement ?? {};
  return { centered, panelProps: { ref: attachPanel, style } };
}

export interface PopoverTabsOptions {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerRef: RefObject<HTMLElement | null>;
  /** Panel id, when the caller needs to name it; generated otherwise. */
  id?: string;
}

/** Keyboard routing between a trigger and its panel, which is portaled out of the
 *  tab order. Spread triggerProps on the trigger and popoverProps on the Popover. */
export function usePopoverTabs({ open, setOpen, triggerRef, id }: PopoverTabsOptions) {
  const generatedId = useId();
  const panelId = id ?? generatedId;

  // Read off the live panel: what is focusable there is the panel's business.
  // FOCUSABLE already drops disabled controls; the filter drops hidden ones.
  const controls = () => {
    const panel = document.getElementById(panelId);
    if (!panel) return [];
    return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
      .filter((el) => el.offsetParent !== null || el === document.activeElement);
  };

  const focusEdge = (edge: 'first' | 'last') => {
    const items = controls();
    (edge === 'first' ? items[0] : items[items.length - 1])?.focus();
  };

  const closeAndRefocus = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onTriggerKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      // Closed, the panel does not exist yet, so an empty list proves nothing.
      if (open && !controls().length) return;
      e.preventDefault();
      const edge = e.key === 'ArrowDown' ? 'first' : 'last';
      setOpen(true);
      // The panel only exists once the open has committed.
      queueMicrotask(() => focusEdge(edge));
      return;
    }
    if (e.key !== 'Tab' || !open) return;
    // Shift+Tab drops the panel and lets focus carry on backward.
    if (e.shiftKey) {
      setOpen(false);
      return;
    }
    const items = controls();
    if (!items.length) return;
    e.preventDefault();
    items[0].focus();
  };

  const onPanelKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key !== 'Tab') return;
    const items = controls();
    const edge = e.shiftKey ? items[0] : items[items.length - 1];
    // No preventDefault: focus lands on the trigger and the browser's own Tab
    // carries on past it, the way a menu button is meant to behave.
    if (document.activeElement === edge) closeAndRefocus();
  };

  return {
    controls,
    closeAndRefocus,
    triggerProps: {
      onKeyDown: onTriggerKeyDown,
      'aria-expanded': open,
      'aria-controls': open ? panelId : undefined,
    },
    popoverProps: {
      id: panelId,
      onKeyDown: onPanelKeyDown,
      // Escape came from the keyboard, so focus goes back to the trigger; an
      // outside click or a vanished anchor leaves focus where the user put it.
      onClose: (reason: PopoverCloseReason) => (
        reason === 'escape' ? closeAndRefocus() : setOpen(false)
      ),
    },
  };
}
