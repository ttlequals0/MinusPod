import { useEffect, useId, useRef, useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getErrorMessage } from '../api/client';
import { DEMO_AUDIO_TOOLTIP } from '../demo/copy';
import { IS_DEMO } from '../demo/flag';
import { AudioMatch, AudioMatchesPage, getPatternAudioMatches, invalidatePatternAudio, submitCorrection } from '../api/patterns';
import type { PatternAudioPreview } from '../hooks/usePatternAudioPreview';
import { formatElapsed, formatPercent, formatTimestamp } from '../utils/format';
import { AuditionPlayButton } from './AuditionPlayButton';
import { badgeBase, tint } from './badgeStyles';
import { btnOutline } from './buttonStyles';
import { DemoDisabled } from './DemoDisabled';
import { Pagination } from './Pagination';
import QueryError from './QueryError';
import { Skeleton } from './Skeleton';
import { rowCard } from './tableStyles';
import { rowActionBtn } from './rowActionStyles';
import { subHeading } from './pageStyles';

const PER_PAGE = 10;

function outcome(m: AudioMatch): { label: string; tone: keyof typeof tint } {
  if (m.rejected) return { label: 'Not this ad', tone: 'destructive' };
  if (!m.inScope) return { label: 'Out of scope', tone: 'warning' };
  if (m.audioOnly) return { label: 'Found by audio only', tone: 'primary' };
  return { label: 'Matched', tone: 'neutral' };
}

// Seconds up to ten minutes so the two numbers compare at a glance.
const clipLength = (s: number) => (s < 600 ? `${Math.round(s)}s` : formatElapsed(s));

function covered(m: AudioMatch): string {
  const found = clipLength(m.end - m.start);
  return m.storedLength != null ? `${found} of ${clipLength(m.storedLength)}` : found;
}

export default function AudioMatchesList({ patternId, preview }: { patternId: number; preview: PatternAudioPreview }) {
  const queryClient = useQueryClient();
  const headingId = useId();
  const listRef = useRef<HTMLUListElement>(null);
  const [focusMatchId, setFocusMatchId] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [rejectError, setRejectError] = useState<string | null>(null);
  const matchesKey = ['pattern-audio-matches', patternId] as const;

  const { data, isPending, isError, refetch, isFetching, isPlaceholderData } = useQuery({
    queryKey: [...matchesKey, page],
    queryFn: () => getPatternAudioMatches(patternId, { page, perPage: PER_PAGE }),
    placeholderData: keepPreviousData,
  });

  const reject = useMutation({
    mutationFn: (m: AudioMatch) => submitCorrection(m.podcastSlug, m.episodeId, {
      type: 'reject',
      original_ad: { start: m.start, end: m.end, pattern_id: patternId },
    }),
    onMutate: () => {
      preview.stop();
      setRejectError(null);
    },
    onError: (error) => setRejectError(getErrorMessage(error, 'Could not save correction.')),
    onSuccess: (_void, m) => {
      setFocusMatchId(m.id);
      queryClient.setQueryData<AudioMatchesPage>([...matchesKey, page], (old) => old && {
        ...old, matches: old.matches.map((x) => (x.id === m.id ? { ...x, rejected: true } : x)),
      });
      invalidatePatternAudio(queryClient, patternId);
      queryClient.invalidateQueries({ queryKey: ['detections'] });
      queryClient.invalidateQueries({ queryKey: ['episode', m.podcastSlug, m.episodeId] });
      // Rejecting a cut ad puts the audio back, which the server flags for a recut.
      queryClient.invalidateQueries({ queryKey: ['pending-recuts'] });
    },
  });

  const goTo = (next: number) => {
    preview.stop();
    setRejectError(null);
    setPage(next);
  };

  // The row's Not this ad button is gone once it is saved; put focus on the row's next control.
  useEffect(() => {
    const row = focusMatchId == null ? null : listRef.current?.querySelector(`[data-match-id="${focusMatchId}"]`);
    const target = row?.querySelector<HTMLElement>('button[aria-label^="Play"]') ?? row?.querySelector<HTMLElement>('h5');
    if (target && data?.matches.find((m) => m.id === focusMatchId)?.rejected) {
      target.focus();
      setFocusMatchId(null);
    }
  }, [data, focusMatchId]);

  let body;
  if (isPending) {
    body = (
      <div role="status" aria-label="Loading matches" aria-busy="true" className="space-y-2">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  } else if (isError || !data) {
    body = <QueryError title="Could not load matches." onRetry={() => { void refetch(); }} retrying={isFetching} />;
  } else if (data.total === 0) {
    body = <p className="text-sm text-muted-foreground">No matches yet.</p>;
  } else {
    body = (
      <>
        {isPlaceholderData && <p role="status" className="text-sm text-muted-foreground">Loading matches...</p>}
        <ul ref={listRef} aria-busy={isPlaceholderData} className="space-y-2">
          {data.matches.map((m) => {
            const { label, tone } = outcome(m);
            const playError = preview.errorFor(m.id);
            const busy = reject.isPending && reject.variables?.id === m.id;
            const title = m.episodeTitle ?? 'Episode no longer available';
            const frozen = isPlaceholderData;
            return (
              <li key={m.id} data-match-id={m.id} className={`${rowCard} space-y-2`}>
                <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                  <h5 tabIndex={-1} className="min-w-0 text-sm font-medium text-foreground break-words rounded focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring">
                    {title}
                  </h5>
                  <span className={`${badgeBase} ${tint[tone]}`}>{label}</span>
                </div>
                <dl className="grid grid-cols-[repeat(auto-fit,minmax(5.5rem,1fr))] gap-x-3 gap-y-1 text-sm">
                  <div>
                    <dt className="text-xs text-muted-foreground">Position</dt>
                    <dd className="text-foreground tabular-nums">{formatTimestamp(m.start)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Match</dt>
                    <dd className="text-foreground">{m.similarity != null ? `${formatPercent(m.similarity)} match` : '-'}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Covered</dt>
                    <dd className="text-foreground">{covered(m)}</dd>
                  </div>
                </dl>
                {m.available ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <AuditionPlayButton
                      playing={preview.isPlaying(m.id)}
                      onClick={() => preview.toggle(m.id)}
                      label={`match at ${formatTimestamp(m.start)}, ${title}`}
                      disabled={frozen || IS_DEMO}
                      title={IS_DEMO ? DEMO_AUDIO_TOOLTIP : undefined}
                    />
                    {!m.rejected && (
                      <DemoDisabled>
                        <button
                          type="button"
                          onClick={() => reject.mutate(m)}
                          disabled={reject.isPending || frozen}
                          aria-busy={busy}
                          aria-label={`Not this ad, ${title}`}
                          className={`${rowActionBtn} ${btnOutline}`}
                        >
                          Not this ad
                        </button>
                      </DemoDisabled>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">Source audio expired</p>
                )}
                {preview.isLoading(m.id) && <p role="status" className="text-sm text-muted-foreground">Loading audio...</p>}
                {playError && <p role="alert" className="text-sm text-destructive">{playError}</p>}
              </li>
            );
          })}
        </ul>
        {rejectError && <p role="alert" className="text-sm text-destructive">{rejectError}</p>}
        <Pagination page={data.page} totalPages={Math.ceil(data.total / data.perPage)} total={data.total} onPage={goTo} />
      </>
    );
  }

  return (
    <section aria-labelledby={headingId} className="space-y-3">
      <h4 id={headingId} className={subHeading}>Recent matches</h4>
      {body}
    </section>
  );
}
