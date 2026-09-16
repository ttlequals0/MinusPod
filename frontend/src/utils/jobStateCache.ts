import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import type { Episode, EpisodeDetail, EpisodeSummary, Feed, JobState } from '../api/types';

interface EpisodesPage {
  episodes: Episode[];
}

interface FeedsPage {
  feeds: Feed[];
}

// The jobState a 409 conflict reported. Success bodies go through
// jobStateOf directly.
export function jobStateFromError(error: unknown): JobState | undefined {
  return error instanceof ApiError ? error.jobState : undefined;
}

// Writes a server-reported jobState straight into the cached episode detail,
// episode lists, and dashboard feed projection. A refetch can fail or be slow;
// without this the affected rows keep advertising stale eligibility.
export function applyEpisodeJobState(
  queryClient: QueryClient,
  slug: string,
  episodeIds: string[],
  jobState: JobState | undefined,
): void {
  if (!jobState || episodeIds.length === 0) return;
  const ids = new Set(episodeIds);
  const patch = <T extends { id: string; jobState?: JobState }>(episode: T): T =>
    ids.has(episode.id) ? { ...episode, jobState } : episode;

  for (const id of ids) {
    queryClient.setQueryData<EpisodeDetail>(
      ['episode', slug, id],
      (current) => (current ? { ...current, jobState } : current),
    );
  }

  queryClient.setQueriesData<EpisodesPage>(
    { queryKey: ['episodes', slug] },
    (current) => (current?.episodes
      ? { ...current, episodes: current.episodes.map(patch) }
      : current),
  );

  queryClient.setQueriesData<FeedsPage>(
    { queryKey: ['feeds'] },
    (current) => (current?.feeds
      ? {
        ...current,
        feeds: current.feeds.map((feed) => (feed.slug === slug && feed.latestEpisodes
          ? { ...feed, latestEpisodes: feed.latestEpisodes.map<EpisodeSummary>(patch) }
          : feed)),
      }
      : current),
  );
}
