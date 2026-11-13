import { useEffect, useId, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AdPattern, AudioDetail, deletePatternAudio, getPatternAudio, invalidatePatternAudio, patternAudioKey, setPatternAudioEnabled,
} from '../api/patterns';
import { getEpisode } from '../api/feeds';
import { DEMO_AUDIO_TOOLTIP } from '../demo/copy';
import { IS_DEMO } from '../demo/flag';
import { usePatternAudioPreview } from '../hooks/usePatternAudioPreview';
import { usePatternAudioRebuild } from '../hooks/usePatternAudioRebuild';
import { formatDateTimeParts, formatPercent } from '../utils/format';
import AdReviewModal from './AdReviewModal';
import AudioMatchesList from './AudioMatchesList';
import { AuditionPlayButton } from './AuditionPlayButton';
import { btnBase, btnDestructiveOutline, btnOutline } from './buttonStyles';
import { ConfirmModal } from './Modal';
import { DemoDisabled } from './DemoDisabled';
import QueryError from './QueryError';
import { Skeleton } from './Skeleton';
import StatTile from './StatTile';
import ToggleSwitch from './ToggleSwitch';
import { subHeading } from './pageStyles';

// A pattern without audio can be built from the ad it matched, when the server knows which.
function AddFromSource({ patternId, source, onAdded }: {
  patternId: number;
  source: NonNullable<AudioDetail['addSource']>;
  onAdded: () => void;
}) {
  const rebuild = usePatternAudioRebuild(patternId, onAdded);
  const { podcastSlug, episodeId, start, end } = source;
  return (
    <div className="flex flex-col items-start gap-1">
      <DemoDisabled>
        <button
          type="button"
          onClick={() => rebuild.mutate({ podcastSlug, episodeId, start, end })}
          disabled={!source.available || rebuild.isPending}
          aria-busy={rebuild.isPending}
          className={`${btnBase} ${btnOutline}`}
        >
          {rebuild.isPending ? 'Adding audio...' : 'Add audio to pattern'}
        </button>
      </DemoDisabled>
      {!source.available && <p className="text-xs text-muted-foreground">Source audio expired</p>}
      {rebuild.isError && <p role="alert" className="text-sm text-destructive">{rebuild.error.message}</p>}
    </div>
  );
}

const EDGE_SLACK_SECONDS = 30;

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
  const [editingEdges, setEditingEdges] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const editEdgesRef = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  // Which saved state the heading waits for before it takes focus: the control that had it is gone by then.
  const focusHeadingWhen = useRef<'none' | 'present' | null>(null);

  const { data: detail, isPending, isError, refetch, isFetching } = useQuery({
    queryKey: patternAudioKey(pattern.id),
    queryFn: () => getPatternAudio(pattern.id),
  });

  const built = detail?.state !== 'none' ? detail?.builtFrom : null;
  const sourceAvailable = !!built?.available;
  const episodeQuery = useQuery({
    queryKey: ['episode', built?.podcastSlug, built?.episodeId],
    queryFn: () => getEpisode(built!.podcastSlug, built!.episodeId),
    // Only Edit edges needs the duration, so the episode loads on that click.
    enabled: sourceAvailable && editingEdges,
  });

  const rebuild = usePatternAudioRebuild(pattern.id, () => {
    setEditingEdges(false);
    setNotice('Audio edges saved.');
  });

  const refresh = () => {
    invalidatePatternAudio(queryClient, pattern.id);
  };

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => setPatternAudioEnabled(pattern.id, enabled),
    onMutate: () => setNotice(''),
    onSuccess: (next: AudioDetail, enabled) => {
      queryClient.setQueryData(patternAudioKey(pattern.id), next);
      queryClient.invalidateQueries({ queryKey: ['patterns'] });
      setNotice(`Audio matching turned ${enabled ? 'on' : 'off'}.`);
    },
    // A failure may mean the fingerprint is gone, so show what the server holds.
    onError: () => { queryClient.invalidateQueries({ queryKey: patternAudioKey(pattern.id) }); },
  });

  const remove = useMutation({
    mutationFn: () => deletePatternAudio(pattern.id),
    onMutate: () => setDeleteFailed(false),
    onSuccess: () => {
      preview.stop();
      setShowDelete(false);
      setNotice('Audio deleted.');
      focusHeadingWhen.current = 'none';
      refresh();
    },
    onError: () => {
      setShowDelete(false);
      setDeleteFailed(true);
      queryClient.invalidateQueries({ queryKey: patternAudioKey(pattern.id) });
    },
  });

  useEffect(() => {
    const waiting = focusHeadingWhen.current;
    if (waiting && detail && (waiting === 'none') === (detail.state === 'none')) {
      headingRef.current?.focus();
      focusHeadingWhen.current = null;
    }
  }, [detail]);

  // The editor takes focus with it when it closes; hand it back to the button that opened it.
  useEffect(() => {
    if (wasEditing.current && !editingEdges) editEdgesRef.current?.focus();
    wasEditing.current = editingEdges;
  }, [editingEdges]);

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
    body = (
      <>
        <p className="text-sm text-muted-foreground">No audio fingerprint.</p>
        {!detail.addSource && (
          <p className="text-sm text-muted-foreground">
            No retained episode has this ad. To add audio, use Add audio to pattern on a text pattern detection in the Detected ads tab or on the episode page.
          </p>
        )}
        {detail.addSource && (
          <AddFromSource
            patternId={pattern.id}
            source={detail.addSource}
            onAdded={() => { focusHeadingWhen.current = 'present'; setNotice('Audio added to pattern.'); }}
          />
        )}
      </>
    );
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
          <span className="text-sm text-muted-foreground">Applies immediately.</span>
          <AuditionPlayButton
            playing={preview.isPlaying()}
            onClick={() => preview.toggle()}
            label="source audio"
            disabled={!playable || IS_DEMO}
            title={IS_DEMO ? DEMO_AUDIO_TOOLTIP : playable ? undefined : 'No source audio'}
          />
          {!playable && <span className="text-sm text-muted-foreground">No source audio</span>}
        </div>
        {toggle.isError && (
          <p role="alert" className="text-sm text-destructive">
            {`Could not turn audio ${toggle.variables ? 'on' : 'off'}.`}
          </p>
        )}
        {detail.matchable === false && (
          <p className="text-sm text-muted-foreground">This audio cannot match under the current settings.</p>
        )}
        {preview.isLoading() && <p role="status" className="text-sm text-muted-foreground">Loading audio...</p>}
        {playError && <p role="alert" className="text-sm text-destructive">{playError}</p>}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-4">
          <StatTile label="Matches" value={detail.counts.matches} />
          <StatTile label="Mean match" value={detail.meanSimilarity != null ? formatPercent(detail.meanSimilarity) : '-'} />
          <StatTile label="Not this ad" value={detail.counts.rejected} tone={detail.counts.rejected > 0 ? 'destructive' : 'default'} />
          <StatTile label="Last audio match" value={(
            <span className="block text-sm font-semibold leading-tight">
              {detail.lastMatchedAt ? (
                <>
                  <span className="block">{formatDateTimeParts(detail.lastMatchedAt).date}</span>
                  <span className="block">{formatDateTimeParts(detail.lastMatchedAt).time}</span>
                </>
              ) : '-'}
            </span>
          )} />
        </div>
        {detail.historyBackfillPending && (
          <p className="text-sm text-muted-foreground">Match history is still being filled in.</p>
        )}
        {detail.counts.outOfScope > 0 && (
          <p className="text-sm text-muted-foreground">{outOfScopeNote(detail.counts.outOfScope)}</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {built && (
            <>
              <DemoDisabled>
                <button
                  ref={editEdgesRef}
                  type="button"
                  onClick={() => {
                    preview.stop();
                    rebuild.reset();
                    setEditingEdges(true);
                    if (episodeQuery.isError) void episodeQuery.refetch();
                  }}
                  disabled={!sourceAvailable || episodeQuery.isLoading}
                  aria-busy={episodeQuery.isLoading}
                  className={`${btnBase} ${btnOutline}`}
                >
                  Edit edges
                </button>
              </DemoDisabled>
              {!sourceAvailable && <span className="text-sm text-muted-foreground">Source audio expired</span>}
              {sourceAvailable && episodeQuery.isError && (
                <span role="alert" className="text-sm text-destructive">Could not load the source episode.</span>
              )}
            </>
          )}
          {!built && <span className="text-sm text-muted-foreground">Edit edges needs source audio.</span>}
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
    <section aria-labelledby={headingId} className="space-y-3">
      <h3 id={headingId} ref={headingRef} tabIndex={-1} className={`${subHeading} focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring rounded`}>Audio</h3>
      <p role="status" aria-label="Audio change" className="sr-only">{notice}</p>
      {body}
      {preview.audioElement}
      {editingEdges && built && episodeQuery.data && (
        <AdReviewModal
          item={{
            podcastSlug: built.podcastSlug,
            episodeId: built.episodeId,
            start: built.start,
            end: built.end,
            sponsor: null,
            reason: null,
            confidence: null,
            detectionStage: null,
            patternId: null,
            correctedBounds: null,
          }}
          mode="span"
          onClose={() => setEditingEdges(false)}
          onSubmit={() => {}}
          onSkip={() => {}}
          onSaveSpan={async (span) => { await rebuild.mutateAsync({ podcastSlug: built.podcastSlug, episodeId: built.episodeId, ...span }); }}
          hasOriginal
          // Without a known duration the editor still needs a bound past the span.
          episodeDuration={episodeQuery.data.originalDuration || built.end + EDGE_SLACK_SECONDS}
        />
      )}
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
