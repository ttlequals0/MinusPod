import { useEffect, useRef, useState } from 'react';
import type { AudioSpan, PatternAudioState } from '../api/patterns';
import { usePatternAudioRebuild } from '../hooks/usePatternAudioRebuild';
import { btnOutline } from './buttonStyles';
import { DemoDisabled } from './DemoDisabled';
import { focusRing } from './fieldStyles';
import { rowActionBtn } from './rowActionStyles';

/** Offers audio for a text-pattern detection whose pattern has none, built from the detection's span. */
export default function AddAudioToPatternButton({
  patternId, audioState, span, hasOriginal, buttonClassName = `${rowActionBtn} ${btnOutline}`, className = '',
}: {
  patternId: number;
  audioState?: PatternAudioState;
  span: AudioSpan;
  hasOriginal: boolean;
  buttonClassName?: string;
  className?: string;
}) {
  const [added, setAdded] = useState(false);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const rebuild = usePatternAudioRebuild(patternId, () => setAdded(true));

  // The button unmounts on success; the status line is the stable element that takes focus.
  useEffect(() => {
    if (added) statusRef.current?.focus();
  }, [added]);

  if (audioState !== 'none' && !added) return null;

  return (
    <div className={`flex flex-col items-start gap-1 ${className}`}>
      {!added && (
        <>
          <DemoDisabled>
            <button
              type="button"
              onClick={() => rebuild.mutate(span)}
              disabled={!hasOriginal || rebuild.isPending}
              aria-busy={rebuild.isPending}
              className={buttonClassName}
            >
              {rebuild.isPending ? 'Adding audio...' : 'Add audio to pattern'}
            </button>
          </DemoDisabled>
          {!hasOriginal && <p className="text-xs text-muted-foreground">Source audio expired</p>}
          {rebuild.isError && <p role="alert" className="text-xs text-destructive">{rebuild.error.message}</p>}
        </>
      )}
      <p
        ref={statusRef}
        role="status"
        tabIndex={-1}
        className={`text-xs text-muted-foreground empty:hidden rounded ${focusRing}`}
      >
        {added ? 'Audio added to pattern.' : ''}
      </p>
    </div>
  );
}
