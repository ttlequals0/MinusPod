import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getPatterns } from '../api/patterns';
import { usePatternAudioRebuild, type AudioSourceSpan } from '../hooks/usePatternAudioRebuild';
import { btnOutline } from './buttonStyles';
import { DemoDisabled } from './DemoDisabled';
import { rowActionBtn } from './rowActionStyles';

// One request for every row on the page: rows share the key and read their own pattern from it.
export const patternAudioStatesKey = ['patterns', 'audio-states'] as const;

/** Offers audio for a text-pattern detection whose pattern has none, built from the detection's span. */
export default function AddAudioToPatternButton({
  patternId, span, hasOriginal, buttonClassName = `${rowActionBtn} ${btnOutline}`, className = '',
}: {
  patternId: number;
  span: AudioSourceSpan;
  hasOriginal: boolean;
  buttonClassName?: string;
  className?: string;
}) {
  const [added, setAdded] = useState(false);
  const { data: states } = useQuery({
    queryKey: patternAudioStatesKey,
    queryFn: () => getPatterns(),
    select: (list) => new Map(list.map((p) => [p.id, p.audio?.state])),
  });
  const rebuild = usePatternAudioRebuild(patternId, () => setAdded(true));

  if (added) {
    return <p role="status" className={`text-xs text-muted-foreground ${className}`}>Audio added to pattern.</p>;
  }
  if (states?.get(patternId) !== 'none') return null;

  return (
    <div className={`flex flex-col items-start gap-1 ${className}`}>
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
      {!hasOriginal && <p className="text-xs text-muted-foreground">Original audio not kept</p>}
      {rebuild.isError && <p role="alert" className="text-xs text-destructive">{rebuild.error.message}</p>}
    </div>
  );
}
