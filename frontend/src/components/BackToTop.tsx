import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp } from 'lucide-react';
import { btnSecondary, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';

const SHOW_AFTER_VIEWPORTS = 1.5;
const SIZE_PX = 44;
const MARGIN_PX = 16;
const CONTROL = 'a[href], button, input, select, textarea, summary, [role="tab"], [role="button"], [tabindex]:not([tabindex="-1"])';

// Fixed bars mark themselves with data-viewport-inset; the button sits above their summed height.
const bottomInset = () => Array.from(document.querySelectorAll<HTMLElement>('[data-viewport-inset="bottom"]'))
  .reduce((total, bar) => total + bar.getBoundingClientRect().height, 0);
const dialogOpen = () => document.querySelector('[role="dialog"][aria-modal="true"]') !== null;

function focusMain() {
  const target = document.querySelector<HTMLElement>('main h1') ?? document.getElementById('main');
  if (!target) return;
  if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
  target.focus({ preventScroll: true });
}

// True when an interactive control sits where the button would be, so the button steps aside instead of covering it.
function coversControl(inset: number, own: HTMLElement | null): boolean {
  const right = window.innerWidth - MARGIN_PX;
  const bottom = window.innerHeight - (inset > 0 ? inset : 0) - MARGIN_PX;
  for (const fx of [0, 0.5, 1]) {
    for (const fy of [0, 0.5, 1]) {
      const x = right - SIZE_PX * fx;
      const y = bottom - SIZE_PX * fy;
      if ((document.elementsFromPoint?.(x - 0.5, y - 0.5) ?? []).some((el) => el !== own && !own?.contains(el) && el.closest(CONTROL))) return true;
    }
  }
  return false;
}

function BackToTop() {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [scrolled, setScrolled] = useState(false);
  const [inset, setInset] = useState(0);
  const [modal, setModal] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const insetRef = useRef(0);

  useEffect(() => {
    const onScroll = () => {
      const next = window.scrollY > window.innerHeight * SHOW_AFTER_VIEWPORTS;
      // The focused button is about to unmount; keep focus in the page.
      if (!next && buttonRef.current && buttonRef.current === document.activeElement) focusMain();
      setScrolled(next);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, []);

  // Observe layout only while the button can show; also re-check for a control under it as the page moves.
  useEffect(() => {
    if (!scrolled) return;
    let frame = 0;
    const bars = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => schedule());
    const measure = () => {
      frame = 0;
      insetRef.current = bottomInset();
      setInset(insetRef.current);
      setModal(dialogOpen());
      setBlocked(coversControl(insetRef.current, buttonRef.current));
      bars?.disconnect();
      document.querySelectorAll('[data-viewport-inset="bottom"]').forEach((bar) => bars?.observe(bar));
    };
    function schedule() { if (!frame) frame = requestAnimationFrame(measure); }
    measure();
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, { passive: true });
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', schedule);
      bars?.disconnect();
      window.removeEventListener('resize', schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [scrolled]);

  const goTop = useCallback(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
    focusMain();
  }, []);

  if (!scrolled || modal || (blocked && buttonRef.current !== document.activeElement)) return null;
  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={goTop}
      aria-label="Back to top"
      title="Back to top"
      style={{ bottom: inset > 0 ? `calc(${inset}px + 1rem)` : 'calc(1rem + env(safe-area-inset-bottom))' }}
      className={`${touchTarget} fixed z-30 right-[max(1rem,env(safe-area-inset-right))] h-11 w-11 rounded-full border border-border shadow-lg ${btnSecondary} ${focusRing}`}
    >
      <ArrowUp className="w-5 h-5" aria-hidden="true" />
    </button>
  );
}

export default BackToTop;
