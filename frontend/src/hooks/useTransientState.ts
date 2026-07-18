import { useCallback, useEffect, useRef, useState } from 'react';

// Show-then-auto-reset state ("Saved" notices, copied checkmarks, status
// flashes). Setting any non-initial value schedules a reset back to
// `initial` after `defaultMs`, or a per-call override; pass null to persist
// a value with no auto-reset (e.g. an in-flight 'loading'). The pending
// timer is cleared on unmount and whenever set() is called, so a stale
// reset can never fire on unmounted or superseded state. A nonce is stored
// alongside the value so re-setting the same value restarts the timer
// (a second "Saved" flash gets its full duration, not the first timer's
// remainder).
export function useTransientState<T>(
  initial: T,
  defaultMs: number,
): [T, (value: T, ms?: number | null) => void] {
  const [entry, setEntry] = useState<{ value: T; nonce: number }>({
    value: initial,
    nonce: 0,
  });
  const msRef = useRef<number | null>(defaultMs);

  useEffect(() => {
    if (entry.value === initial || msRef.current === null) return;
    const timer = setTimeout(
      () => setEntry((prev) => ({ value: initial, nonce: prev.nonce })),
      msRef.current,
    );
    return () => clearTimeout(timer);
  }, [entry, initial]);

  const set = useCallback(
    (v: T, ms: number | null = defaultMs) => {
      msRef.current = ms;
      setEntry((prev) => ({ value: v, nonce: prev.nonce + 1 }));
    },
    [defaultMs],
  );

  return [entry.value, set];
}
