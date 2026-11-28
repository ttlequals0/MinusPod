import { useCallback, useEffect, useState } from 'react';
import { ArrowUp } from 'lucide-react';
import { btnSecondary, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';

const SHOW_AFTER_VIEWPORTS = 1.5;

// Fixed bars mark themselves with data-viewport-inset; the button sits above their summed height.
const bottomInset = () => Array.from(document.querySelectorAll<HTMLElement>('[data-viewport-inset="bottom"]'))
  .reduce((total, bar) => total + bar.getBoundingClientRect().height, 0);
const dialogOpen = () => document.querySelector('[role="dialog"][aria-modal="true"]') !== null;

function BackToTop() {
  const [scrolled, setScrolled] = useState(false);
  const [inset, setInset] = useState(0);
  const [modal, setModal] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > window.innerHeight * SHOW_AFTER_VIEWPORTS);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
  }, []);

  useEffect(() => {
    let frame = 0;
    const measure = () => {
      frame = 0;
      setInset(bottomInset());
      setModal(dialogOpen());
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    measure();
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', schedule);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  const goTop = useCallback(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
    const target = document.querySelector<HTMLElement>('main h1') ?? document.getElementById('main');
    if (target) {
      if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: true });
    }
  }, []);

  if (!scrolled || modal) return null;
  return (
    <button
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
