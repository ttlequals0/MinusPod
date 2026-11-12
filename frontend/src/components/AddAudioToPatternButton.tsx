import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getPatterns, type PatternAudioState } from '../api/patterns';
import { usePatternAudioRebuild, type AudioSourceSpan } from '../hooks/usePatternAudioRebuild';
import { btnOutline } from './buttonStyles';
import { DemoDisabled } from './DemoDisabled';
import { focusRing } from './fieldStyles';
import { rowActionBtn } from './rowActionStyles';

// Fallback for servers that predate patternAudioState on rows: one request for every row on
// the page, each reading its own pattern. Remove once those servers are out of support.
export const patternAudioStatesKey = ['patterns', 'audio-states'] as const;

/** Offers audio for a text-pattern detection whose pattern has none, built from the detection's span. */
export default function AddAudioToPatternButton({
  patternId, audioState, span, hasOriginal, buttonClassName = `${rowActionBtn} ${btnOutline}`, className = '',
}: {
  patternId: number;
  audioState?: PatternAudioState;
  span: AudioSourceSpan;
  hasOriginal: boolean;
  buttonClassName?: string;
  className?: string;
}) {
  const [added, setAdded] = useState(false);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const { data: fallbackState } = useQuery({
    queryKey: patternAudioStatesKey,
    queryFn: () => getPatterns(),
    select: (list) => new Map(list.map((p) => [p.id, p.audio?.state])),
    enabled: audioState === undefined,
  });
  const rebuild = usePatternAudioRebuild(patternId, () => setAdded(true));
  const state = audioState ?? fallbackState?.get(patternId);

  // The button unmounts on success; the status line is the stable element that takes focus.
  useEffect(() => {
    if (added) statusRef.current?.focus();
  }, [added]);

  if (state !== 'none' && !added) return null;

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
