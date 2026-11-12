import { useId, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AdPattern, AudioDetail, deletePatternAudio, getPatternAudio, patternAudioKey, setPatternAudioEnabled,
} from '../api/patterns';
import { usePatternAudioPreview } from '../hooks/usePatternAudioPreview';
import { formatDateTime, formatPercent } from '../utils/format';
import AudioMatchesList from './AudioMatchesList';
import { AuditionPlayButton } from './AuditionPlayButton';
import { btnBase, btnDestructiveOutline } from './buttonStyles';
import { ConfirmModal } from './Modal';
import { DemoDisabled } from './DemoDisabled';
import QueryError from './QueryError';
import { Skeleton } from './Skeleton';
import StatTile from './StatTile';
import ToggleSwitch from './ToggleSwitch';
import { subHeading } from './pageStyles';

function outOfScopeNote(count: number): string {
  return count === 1
    ? "1 match came from an episode outside this pattern's scope."
    : `${count} matches came from episodes outside this pattern's scope.`;
}

export default function PatternAudioSection({ pattern }: { pattern: AdPattern }) {
  const queryClient = useQueryClient();
  const headingId = useId();
  const preview = usePatternAudioPreview(pattern.id);
  const [showDelete, setShowDelete] = useState(false);
  const [notice, setNotice] = useState('');
  const [deleteFailed, setDeleteFailed] = useState(false);

  const { data: detail, isPending, isError, refetch, isFetching } = useQuery({
    queryKey: patternAudioKey(pattern.id),
    queryFn: () => getPatternAudio(pattern.id),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['patterns'] });
    queryClient.invalidateQueries({ queryKey: patternAudioKey(pattern.id) });
    queryClient.invalidateQueries({ queryKey: ['pattern-audio-matches', pattern.id] });
  };

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => setPatternAudioEnabled(pattern.id, enabled),
    onMutate: () => setNotice(''),
    onSuccess: (next: AudioDetail, enabled) => {
      queryClient.setQueryData(patternAudioKey(pattern.id), next);
      queryClient.invalidateQueries({ queryKey: ['patterns'] });
      setNotice(`Audio matching turned ${enabled ? 'on' : 'off'}.`);
    },
  });

  const remove = useMutation({
    mutationFn: () => deletePatternAudio(pattern.id),
    onMutate: () => setDeleteFailed(false),
    onSuccess: () => {
      preview.stop();
      setShowDelete(false);
      setNotice('Audio deleted.');
      refresh();
    },
    onError: () => {
      setShowDelete(false);
      setDeleteFailed(true);
    },
  });

  let body;
  if (isPending) {
    body = (
      <div role="status" aria-label="Loading audio" aria-busy="true" className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  } else if (isError || !detail) {
    body = <QueryError title="Could not load audio." onRetry={() => { void refetch(); }} retrying={isFetching} />;
  } else if (detail.state === 'none') {
    body = <p className="text-sm text-muted-foreground">No audio fingerprint.</p>;
  } else {
    // While a change is in flight the switch shows the requested state, and returns to the saved one if it fails.
    const on = toggle.isPending ? toggle.variables : detail.state === 'active';
    const playable = !!detail.source;
    const playError = preview.errorFor();
    body = (
      <>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <DemoDisabled>
            <ToggleSwitch checked={on} onChange={(v) => toggle.mutate(v)} disabled={toggle.isPending} ariaLabel="Audio matching" />
          </DemoDisabled>
          <span className="text-sm text-foreground">Audio matching</span>
          <span className="text-sm text-muted-foreground">{on ? 'Audio on' : 'Audio off'}</span>
          <AuditionPlayButton
            playing={preview.isPlaying()}
            onClick={() => preview.toggle()}
            label="source audio"
            disabled={!playable}
            title={playable ? undefined : 'No source audio'}
          />
          {!playable && <span className="text-sm text-muted-foreground">No source audio</span>}
        </div>
        {toggle.isError && (
          <p role="alert" className="text-sm text-destructive">
            {`Could not turn audio ${toggle.variables ? 'on' : 'off'}.`}
          </p>
        )}
        {playError && <p role="alert" className="text-sm text-destructive">{playError}</p>}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(6rem,1fr))] gap-2 sm:gap-4">
          <StatTile label="Matches" value={detail.counts.matches} />
          <StatTile label="Mean match" value={detail.meanSimilarity != null ? formatPercent(detail.meanSimilarity) : '-'} />
          <StatTile label="Not this ad" value={detail.counts.rejected} tone={detail.counts.rejected > 0 ? 'destructive' : 'default'} />
          <StatTile label="Last audio match" value={<span className="text-sm font-semibold break-words">{formatDateTime(detail.lastMatchedAt ?? null)}</span>} />
        </div>
        {detail.counts.outOfScope > 0 && (
          <p className="text-sm text-muted-foreground">{outOfScopeNote(detail.counts.outOfScope)}</p>
        )}
        <div className="flex flex-wrap gap-2">
          <DemoDisabled>
            <button type="button" onClick={() => setShowDelete(true)} className={`${btnBase} ${btnDestructiveOutline}`}>
              Delete audio
            </button>
          </DemoDisabled>
        </div>
        {deleteFailed && <p role="alert" className="text-sm text-destructive">Could not delete audio.</p>}
        <AudioMatchesList patternId={pattern.id} preview={preview} />
      </>
    );
  }

  return (
    <section aria-labelledby={headingId} className="border-t border-border pt-4 space-y-3">
      <h3 id={headingId} className={subHeading}>Audio</h3>
      <p role="status" aria-label="Audio change" className="sr-only">{notice}</p>
      {body}
      {preview.audioElement}
      {showDelete && (
        <ConfirmModal
          title="Delete audio?"
          confirmLabel="Delete audio"
          busyLabel="Deleting audio..."
          pending={remove.isPending}
          onCancel={() => setShowDelete(false)}
          onConfirm={() => remove.mutate()}
        >
          <p className="text-sm text-muted-foreground">
            This removes the audio fingerprint and its match history. The pattern and its text stay.
          </p>
        </ConfirmModal>
      )}
    </section>
  );
}
