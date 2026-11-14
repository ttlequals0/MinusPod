import { useEffect, useId, useRef, useState } from 'react';
import { Plus } from 'lucide-react';
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

const NO_SOURCE_REASON = 'No source audio is available. Use Add audio to pattern on a text pattern detection in the Detected ads tab or on the episode page.';

function AddFromSource({ patternId, source, onAdded }: {
  patternId: number;
  source: AudioDetail['addSource'];
  onAdded: () => void;
}) {
  const rebuild = usePatternAudioRebuild(patternId, onAdded);
  const reasonId = useId();
  const reason = !source ? NO_SOURCE_REASON : !source.available ? 'Source audio expired' : null;
  return (
    <div className="flex flex-col items-start gap-1">
      <DemoDisabled>
        <button
          type="button"
          onClick={() => {
            if (source) rebuild.mutate({ podcastSlug: source.podcastSlug, episodeId: source.episodeId, start: source.start, end: source.end });
          }}
          disabled={!!reason || rebuild.isPending}
          aria-busy={rebuild.isPending}
          aria-describedby={reason ? reasonId : undefined}
          className={`${btnBase} ${btnOutline}`}
        >
          <Plus className="w-4 h-4" aria-hidden="true" />
          {rebuild.isPending ? 'Adding audio...' : 'Add audio to pattern'}
        </button>
      </DemoDisabled>
      {reason && <p id={reasonId} className="text-xs text-muted-foreground">{reason}</p>}
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

export default function PatternAudioSection({ pattern, active = true }: { pattern: AdPattern; active?: boolean }) {
  const queryClient = useQueryClient();
  const headingId = useId();
  const sourceReasonId = useId();
  const preview = usePatternAudioPreview(pattern.id);
  const [showDelete, setShowDelete] = useState(false);
  const [notice, setNotice] = useState('');
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

  const editSource = detail?.state !== 'none' ? detail?.editSource : null;
  const sourceAvailable = !!editSource;
  const episodeQuery = useQuery({
    queryKey: ['episode', editSource?.podcastSlug, editSource?.episodeId],
    queryFn: () => getEpisode(editSource!.podcastSlug, editSource!.episodeId),
    // Only Edit edges needs the duration, so the episode loads on that click.
    enabled: sourceAvailable && editingEdges,
  });

  const rebuild = usePatternAudioRebuild(pattern.id, () => {
    preview.reset();
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
    onSuccess: () => {
      preview.reset();
      setShowDelete(false);
      setNotice('Audio deleted.');
      focusHeadingWhen.current = 'none';
      refresh();
    },
    onError: () => {
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

  // A hidden tab must not keep playing
  useEffect(() => { if (!active) preview.stop(); }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

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
        <AddFromSource
          patternId={pattern.id}
          source={detail.addSource ?? null}
          onAdded={() => { focusHeadingWhen.current = 'present'; setNotice('Audio added to pattern.'); }}
        />
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
          <p className="text-sm text-muted-foreground">
            {IS_DEMO ? 'Match history does not update in this demo.' : 'Match history is still being filled in.'}
          </p>
        )}
        {detail.counts.outOfScope > 0 && (
          <p className="text-sm text-muted-foreground">{outOfScopeNote(detail.counts.outOfScope)}</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
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
              aria-describedby={!sourceAvailable ? sourceReasonId : undefined}
              className={`${btnBase} ${btnOutline}`}
            >
              Edit edges
            </button>
          </DemoDisabled>
          {sourceAvailable && episodeQuery.isError && (
            <span role="alert" className="text-sm text-destructive">Could not load the source episode.</span>
          )}
          {!sourceAvailable && (
            <span id={sourceReasonId} className="text-sm text-muted-foreground">
              {detail.builtFrom ? 'Source audio expired' : 'Edit edges needs source audio.'}
            </span>
          )}
          <DemoDisabled>
            <button type="button" onClick={() => { remove.reset(); setShowDelete(true); }} className={`${btnBase} ${btnDestructiveOutline}`}>
              Delete audio
            </button>
          </DemoDisabled>
        </div>
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
      {editingEdges && editSource && episodeQuery.data && (
        <AdReviewModal
          item={{
            podcastSlug: editSource.podcastSlug,
            episodeId: editSource.episodeId,
            start: editSource.start,
            end: editSource.end,
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
          onSaveSpan={async (span) => { await rebuild.mutateAsync({ podcastSlug: editSource.podcastSlug, episodeId: editSource.episodeId, ...span }); }}
          hasOriginal
          // Without a known duration the editor still needs a bound past the span.
          episodeDuration={episodeQuery.data.originalDuration || editSource.end + EDGE_SLACK_SECONDS}
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
          {remove.isError && <p role="alert" className="text-sm text-destructive">Could not delete audio. Try again.</p>}
        </ConfirmModal>
      )}
    </section>
  );
}
