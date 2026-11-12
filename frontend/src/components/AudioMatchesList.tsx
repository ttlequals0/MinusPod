import { useState } from 'react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AudioMatch, AudioMatchesPage, getPatternAudioMatches, patternAudioKey, submitCorrection } from '../api/patterns';
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
import { cardActionBtn } from './rowActionStyles';
import { subHeading } from './pageStyles';

const PER_PAGE = 10;

function outcome(m: AudioMatch): { label: string; tone: keyof typeof tint } {
  if (m.rejected) return { label: 'Not this ad', tone: 'destructive' };
  if (!m.inScope) return { label: 'Out of scope', tone: 'warning' };
  if (m.audioOnly) return { label: 'Found by audio only', tone: 'primary' };
  return { label: 'Matched', tone: 'neutral' };
}

function covered(m: AudioMatch): string {
  const found = formatElapsed(m.end - m.start);
  return m.storedLength != null ? `${found} of ${formatElapsed(m.storedLength)}` : found;
}

export default function AudioMatchesList({ patternId, preview }: { patternId: number; preview: PatternAudioPreview }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const matchesKey = ['pattern-audio-matches', patternId] as const;

  const { data, isPending, isError, refetch, isFetching } = useQuery({
    queryKey: [...matchesKey, page],
    queryFn: () => getPatternAudioMatches(patternId, { page, perPage: PER_PAGE }),
    placeholderData: keepPreviousData,
  });

  const reject = useMutation({
    mutationFn: (m: AudioMatch) => submitCorrection(m.podcastSlug, m.episodeId, {
      type: 'reject',
      original_ad: { start: m.start, end: m.end, pattern_id: patternId },
    }),
    onMutate: () => preview.stop(),
    onSuccess: (_void, m) => {
      queryClient.setQueryData<AudioMatchesPage>([...matchesKey, page], (old) => old && {
        ...old, matches: old.matches.map((x) => (x.id === m.id ? { ...x, rejected: true } : x)),
      });
      queryClient.invalidateQueries({ queryKey: matchesKey });
      queryClient.invalidateQueries({ queryKey: patternAudioKey(patternId) });
      queryClient.invalidateQueries({ queryKey: ['patterns'] });
      // Rejecting a cut ad puts the audio back, which the server flags for a recut.
      queryClient.invalidateQueries({ queryKey: ['pending-recuts'] });
    },
  });

  const goTo = (next: number) => {
    preview.stop();
    setPage(next);
  };

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
        <ul className="space-y-2">
          {data.matches.map((m) => {
            const { label, tone } = outcome(m);
            const playError = preview.errorFor(m.id);
            const busy = reject.isPending && reject.variables?.id === m.id;
            return (
              <li key={m.id} className={`${rowCard} p-3 space-y-2`}>
                <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                  <div className="min-w-0 text-sm font-medium text-foreground break-words">
                    {m.episodeTitle ?? 'Episode no longer available'}
                  </div>
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
                      label={`match at ${formatTimestamp(m.start)}`}
                    />
                    {!m.rejected && (
                      <DemoDisabled>
                        <button
                          type="button"
                          onClick={() => reject.mutate(m)}
                          disabled={reject.isPending}
                          aria-busy={busy}
                          className={`${cardActionBtn} ${btnOutline} disabled:opacity-50`}
                        >
                          Not this ad
                        </button>
                      </DemoDisabled>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">Source audio expired</p>
                )}
                {playError && <p role="alert" className="text-sm text-destructive">{playError}</p>}
              </li>
            );
          })}
        </ul>
        {reject.isError && <p role="alert" className="text-sm text-destructive">Could not save correction.</p>}
        <Pagination page={data.page} totalPages={Math.ceil(data.total / data.perPage)} total={data.total} onPage={goTo} />
      </>
    );
  }

  return (
    <section aria-label="Recent matches" className="space-y-3">
      <h4 className={subHeading}>Recent matches</h4>
      {body}
    </section>
  );
}
