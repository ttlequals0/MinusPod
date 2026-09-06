import { useState } from 'react';
import { useSyncFromQuery } from './useSyncFromQuery';

export interface DraftField {
  value: string;
  setValue: (v: string) => void;
  dirty: boolean;
  // Re-baselines right after a blur commit (or a reset to the server value),
  // so dirty clears immediately instead of waiting on the next refetch.
  markClean: (v: string) => void;
}

// Pairs a draft with the last server-matching baseline so a background refetch
// cannot clobber an in-progress edit. Baseline is state, not a ref, so `dirty`
// tracks the render; `normalize` affects only the compare, never the stored value.
// Reseeds itself from `source` (via useSyncFromQuery) whenever its identity
// changes, unless the draft is dirty, so callers need no per-field sync call.
export function useDraftField<T>(
  source: T,
  selector: (source: T) => string,
  normalize: (v: string) => string = (v) => v,
): DraftField {
  const initial = selector(source);
  const [value, setValue] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const dirty = normalize(value) !== normalize(baseline);

  useSyncFromQuery(source, (s) => {
    if (dirty) return;
    const next = selector(s);
    setBaseline(next);
    setValue(next);
  });

  const markClean = (v: string) => {
    setBaseline(v);
    setValue(v);
  };

  return { value, setValue, dirty, markClean };
}
