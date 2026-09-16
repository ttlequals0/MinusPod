import { act, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';

export const DESKTOP = { width: 1024, height: 768 };
export const PHONE = { width: 375, height: 667 };

interface Viewport { width: number; height: number; }
/** The panel box a placement reads: its width, and the content height that
 *  decides whether it flips above the anchor. */
interface PanelSize { width: number; height: number; }

/** A DOMRect literal for stubbing one element's box. */
export const rect = (r: Partial<DOMRect> = {}) => () => ({
  top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}), ...r,
} as DOMRect);

/** happy-dom reports a zero-sized document, so placement tests set the box a
 *  fixed popover is measured against. Placement reads the document for insets and
 *  window.innerWidth for the phone test; pass innerWidth to tell them apart. */
export function setViewport(width: number, height: number, innerWidth = width) {
  Object.defineProperty(document.documentElement, 'clientWidth', { configurable: true, value: width });
  Object.defineProperty(document.documentElement, 'clientHeight', { configurable: true, value: height });
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: innerWidth });
}

/** Drops what setViewport defined, uncovering the DOM's own getters. */
export function restoreViewport() {
  const root = document.documentElement as unknown as Record<string, unknown>;
  delete root.clientWidth;
  delete root.clientHeight;
  delete (window as unknown as Record<string, unknown>).innerWidth;
}

/** happy-dom lays nothing out, so the panel reports the size a real browser would.
 *  A spy: the calling file's afterEach must run vi.restoreAllMocks. */
export function stubPanelSize(width: number, height: number) {
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(width);
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(height);
}

/** The viewport and panel box every popover test needs before it renders. */
export function setupPlacement(viewport: Viewport, panel: PanelSize) {
  setViewport(viewport.width, viewport.height);
  stubPanelSize(panel.width, panel.height);
}

// Placement coalesces into an animation frame, so both helpers wait one out.
const nextFrame = () => act(async () => {
  await new Promise((resolve) => requestAnimationFrame(resolve));
});

/** Re-runs the placement listeners the way a page scroll or a resize would. */
export const scrollPage = async () => { fireEvent.scroll(window); await nextFrame(); };
export const resizePage = async () => { fireEvent(window, new Event('resize')); await nextFrame(); };
