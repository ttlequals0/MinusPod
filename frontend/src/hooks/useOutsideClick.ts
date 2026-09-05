import { useEffect, useRef, type RefObject } from 'react';

interface Options {
  // Some callers never bind a touch listener today; default true keeps prior callers unchanged.
  touch?: boolean;
  // SponsorInput historically listened on window, not document; default document keeps every other caller unchanged.
  target?: Document | Window;
}

/** Fires onOutside for a mousedown (and by default touchstart) outside ref, only while active. */
export function useOutsideClick(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  onOutside: () => void,
  options?: Options,
): void {
  const touch = options?.touch ?? true;
  const target = options?.target ?? document;

  // Keep the callback current without adding it to the effect's deps, so
  // listeners are only re-attached when `active` flips (matches prior callers).
  const onOutsideRef = useRef(onOutside);
  useEffect(() => {
    onOutsideRef.current = onOutside;
  });

  useEffect(() => {
    if (!active) return;
    // Document | Window addEventListener loses its per-event-name overloads,
    // so the listener is typed against the plain DOM Event here.
    const onPointerDown = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) onOutsideRef.current();
    };
    target.addEventListener('mousedown', onPointerDown);
    if (touch) target.addEventListener('touchstart', onPointerDown);
    return () => {
      target.removeEventListener('mousedown', onPointerDown);
      if (touch) target.removeEventListener('touchstart', onPointerDown);
    };
  }, [active, ref, touch, target]);
}
