import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getFeedProblems, refreshFeed } from '../api/feeds';
import { getErrorMessage } from '../api/client';
import type { FeedProblem } from '../api/types';
import { formatDateTime } from '../utils/format';
import { btnBase, btnSecondary, touchTarget } from './buttonStyles';
import { focusRing } from './fieldStyles';
import { countPill, tint } from './badgeStyles';
import QueryError from './QueryError';

const PROBLEMS_KEY = ['feedProblems'];

export function useFeedProblems() {
  return useQuery({
    queryKey: PROBLEMS_KEY,
    queryFn: getFeedProblems,
    refetchInterval: 60_000,
  });
}

function ProblemRow({ problem, pending, error, onRefresh }: {
  problem: FeedProblem;
  pending: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  return (
    <li className="min-w-0 rounded-lg border border-border bg-card p-3 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
      <div className="min-w-0 space-y-1 text-sm">
        <p className="font-medium text-foreground break-words">
          {problem.title} <span className="font-normal text-muted-foreground break-all">{problem.host}</span>
        </p>
        <p className="text-foreground" title={problem.detail ?? undefined}>{problem.message}</p>
        <p className="text-xs text-muted-foreground">
          {problem.consecutiveFailures} failures in a row
          {'; '}last success {problem.lastSuccessAt ? formatDateTime(problem.lastSuccessAt) : 'never'}
          {'; '}next retry {formatDateTime(problem.nextRetryAt)}
        </p>
        {error && <p role="alert" className="text-xs text-destructive break-words">{error}</p>}
      </div>
      <button
        type="button"
        disabled={pending}
        onClick={onRefresh}
        aria-label={`Refresh ${problem.title} now`}
        className={`${touchTarget} shrink-0 p-0 ${focusRing} disabled:opacity-50 max-md:w-full`}
      >
        <span className={`${btnBase} ${btnSecondary} max-md:w-full`}>
          {pending ? 'Refreshing...' : 'Refresh now'}
        </span>
      </button>
    </li>
  );
}

function FeedProblems() {
  const queryClient = useQueryClient();
  const problems = useFeedProblems();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const refresh = useMutation({
    mutationFn: (slug: string) => refreshFeed(slug),
    onMutate: (slug) => setErrors((current) => ({ ...current, [slug]: '' })),
    onError: (err, slug) => setErrors((current) => ({
      ...current,
      [slug]: getErrorMessage(err, 'Could not refresh this feed.'),
    })),
    onSettled: (_data, _err, slug) => {
      void queryClient.invalidateQueries({ queryKey: PROBLEMS_KEY });
      void queryClient.invalidateQueries({ queryKey: ['feeds'] });
      void queryClient.invalidateQueries({ queryKey: ['feed', slug] });
    },
  });

  const items = problems.data?.problems ?? [];
  return (
    <section aria-labelledby="feed-problems-heading" className="space-y-2">
      <h3 id="feed-problems-heading" className="flex items-center gap-2 text-sm font-medium text-foreground">
        Feed problems
        {items.length > 0 && <span className={`${countPill} ${tint.warning}`}>{items.length}</span>}
      </h3>
      {problems.isError ? (
        <QueryError title="Could not load feed problems." onRetry={() => void problems.refetch()}
          retrying={problems.isFetching} />
      ) : problems.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No feeds are failing.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((problem) => (
            <ProblemRow
              key={problem.slug}
              problem={problem}
              pending={refresh.isPending && refresh.variables === problem.slug}
              error={errors[problem.slug] || null}
              onRefresh={() => refresh.mutate(problem.slug)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

export default FeedProblems;
