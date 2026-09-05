import { useEffect, useRef, type RefObject } from 'react';

interface Options {
  // Some callers never bind a touch listener today; default true keeps prior callers unchanged.
  touch?: boolean;
}

/** Fires onOutside for a mousedown (and by default touchstart) outside ref, only while active. */
export function useOutsideClick(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  onOutside: () => void,
  options?: Options,
): void {
  const touch = options?.touch ?? true;

  // Keep the callback current without adding it to the effect's deps, so
  // listeners are only re-attached when `active` flips (matches prior callers).
  const onOutsideRef = useRef(onOutside);
  useEffect(() => {
    onOutsideRef.current = onOutside;
  });

  useEffect(() => {
    if (!active) return;
    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      if (!ref.current?.contains(e.target as Node)) onOutsideRef.current();
    };
    document.addEventListener('mousedown', onPointerDown);
    if (touch) document.addEventListener('touchstart', onPointerDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      if (touch) document.removeEventListener('touchstart', onPointerDown);
    };
  }, [active, ref, touch]);
}
