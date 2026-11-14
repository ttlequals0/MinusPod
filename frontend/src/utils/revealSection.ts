const SETTLE_MS = 600;
const MAX_MS = 4000;
const USER_EVENTS = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/**
 * Scrolls `el` to the top of the viewport and focuses `focusEl`, then keeps re-aligning while the page layout
 * is still shifting (skeletons giving way to content). Scrolling is instant, so reduced-motion needs no
 * special case. Stops when the layout is quiet, after MAX_MS, or on user input. Returns a cancel function.
 */
export function revealSection(el: HTMLElement, focusEl: HTMLElement | null): () => void {
  let focused = false;
  let settleTimer: ReturnType<typeof setTimeout> | undefined;
  let observer: ResizeObserver | undefined;

  const align = () => {
    // offsetParent is null while the section is display:none (filtered out by search).
    if (!el.isConnected || el.offsetParent === null) return;
    el.scrollIntoView({ block: 'start', behavior: 'auto' });
    if (!focused) {
      focused = true;
      focusEl?.focus({ preventScroll: true });
    }
  };

  const maxTimer = setTimeout(() => stop(), MAX_MS);
  function stop() {
    observer?.disconnect();
    clearTimeout(settleTimer);
    clearTimeout(maxTimer);
    USER_EVENTS.forEach((e) => window.removeEventListener(e, stop));
  }
  const rearmSettle = () => {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(stop, SETTLE_MS);
  };

  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => {
      align();
      rearmSettle();
    });
    observer.observe(document.body);
  }
  USER_EVENTS.forEach((e) => window.addEventListener(e, stop, { passive: true }));

  align();
  rearmSettle();
  return stop;
}
