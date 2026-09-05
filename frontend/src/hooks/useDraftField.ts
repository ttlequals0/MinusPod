import { useState } from 'react';

export interface DraftField {
  value: string;
  setValue: (v: string) => void;
  dirty: boolean;
  // Reseeds from the server unless the draft has an unsaved edit (dirty).
  sync: (serverValue: string) => void;
  // Re-baselines right after a blur commit (or a reset to the server value),
  // so dirty clears immediately instead of waiting on the next refetch.
  markClean: (v: string) => void;
}

// Pairs a locally-typed draft with the last value known to match the server,
// so a background refetch cannot clobber an in-progress edit. Baseline is
// state, not a ref, so `dirty` stays consistent with the render it's read in.
// `normalize` (default identity) is applied to both sides of the dirty
// compare only; it does not affect what `value` holds or what gets saved.
export function useDraftField(initial: string, normalize: (v: string) => string = (v) => v): DraftField {
  const [value, setValue] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const dirty = normalize(value) !== normalize(baseline);

  const sync = (serverValue: string) => {
    if (dirty) return;
    setBaseline(serverValue);
    setValue(serverValue);
  };

  const markClean = (v: string) => {
    setBaseline(v);
    setValue(v);
  };

  return { value, setValue, dirty, sync, markClean };
}
