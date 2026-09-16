import { useEffect, useRef, type RefObject } from 'react';

type OutsideRefs = RefObject<HTMLElement | null> | RefObject<HTMLElement | null>[];

interface Options {
  // Some callers never bind a touch listener today; default true keeps prior callers unchanged.
  touch?: boolean;
  // SponsorInput historically listened on window, not document; default document keeps every other caller unchanged.
  target?: Document | Window;
}

const toList = (r: OutsideRefs) => (Array.isArray(r) ? r : [r]);

/** Fires onOutside for a mousedown (and by default touchstart) outside every ref, only while active. */
export function useOutsideClick(
  refs: OutsideRefs,
  active: boolean,
  onOutside: () => void,
  options?: Options,
): void {
  const touch = options?.touch ?? true;
  const target = options?.target ?? document;

  // Keep the callback and the refs current without adding them to the effect's
  // deps, so listeners are only re-attached when `active` flips.
  const onOutsideRef = useRef(onOutside);
  const refsRef = useRef(toList(refs));
  useEffect(() => {
    onOutsideRef.current = onOutside;
    refsRef.current = toList(refs);
  });

  useEffect(() => {
    if (!active) return;
    // Document | Window addEventListener loses its per-event-name overloads,
    // so the listener is typed against the plain DOM Event here.
    const onPointerDown = (e: Event) => {
      if (!refsRef.current.some((r) => r.current?.contains(e.target as Node))) onOutsideRef.current();
    };
    target.addEventListener('mousedown', onPointerDown);
    if (touch) target.addEventListener('touchstart', onPointerDown);
    return () => {
      target.removeEventListener('mousedown', onPointerDown);
      if (touch) target.removeEventListener('touchstart', onPointerDown);
    };
  }, [active, touch, target]);
}
