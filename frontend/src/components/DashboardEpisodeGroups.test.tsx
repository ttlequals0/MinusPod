import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import DashboardEpisodeGroups, { clampEpisodesPerPodcast } from './DashboardEpisodeGroups';
import type { Feed, EpisodeSummary } from '../api/types';

vi.mock('../api/feeds', () => ({
  reprocessEpisode: vi.fn(async () => ({ message: 'ok', mode: 'reprocess' as const })),
}));

function episodeSummary(overrides: Partial<EpisodeSummary> & { id: string }): EpisodeSummary {
  return {
    title: `Episode ${overrides.id}`,
    published: '2026-09-01T00:00:00Z',
    createdAt: '2026-09-01T00:00:00Z',
    status: 'completed',
    ...overrides,
  };
}

function renderGroups(feeds: Feed[], episodesPerPodcast = 3) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DashboardEpisodeGroups feeds={feeds} episodesPerPodcast={episodesPerPodcast} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DashboardEpisodeGroups', () => {
  it('renders one group per podcast with at most N episode rows', () => {
    const feed: Feed = {
      slug: 'show-a', title: 'Show A', sourceUrl: 'https://example.com/a.xml', feedUrl: 'https://example.com/a.xml',
      episodeCount: 4,
      latestEpisodes: [
        episodeSummary({ id: 'e1' }), episodeSummary({ id: 'e2' }),
        episodeSummary({ id: 'e3' }), episodeSummary({ id: 'e4' }),
      ],
    };
    renderGroups([feed], 3);
    expect(screen.getByRole('heading', { name: 'Show A' })).toBeTruthy();
    expect(screen.getByText('Episode e1')).toBeTruthy();
    expect(screen.getByText('Episode e2')).toBeTruthy();
    expect(screen.getByText('Episode e3')).toBeTruthy();
    expect(screen.queryByText('Episode e4')).toBeNull();
  });

  it('excludes a Recents feed rather than rendering it as an empty group', () => {
    const recents: Feed = {
      slug: 'recents', title: 'Recents', feedType: 'recents',
      sourceUrl: '', feedUrl: 'https://example.com/recents.xml', episodeCount: 10, latestEpisodes: [],
    };
    renderGroups([recents]);
    expect(screen.queryByRole('heading', { name: 'Recents' })).toBeNull();
    expect(screen.getByText('No podcasts to show episodes for yet')).toBeTruthy();
  });

  it('renders a feed with fewer than N episodes cleanly', () => {
    const feed: Feed = {
      slug: 'show-b', title: 'Show B', sourceUrl: 'https://example.com/b.xml', feedUrl: 'https://example.com/b.xml',
      episodeCount: 1, latestEpisodes: [episodeSummary({ id: 'e1' })],
    };
    renderGroups([feed], 3);
    expect(screen.getByText('Episode e1')).toBeTruthy();
  });

  it('renders a feed with no episodes without breaking the group', () => {
    const feed: Feed = {
      slug: 'show-c', title: 'Show C', sourceUrl: 'https://example.com/c.xml', feedUrl: 'https://example.com/c.xml',
      episodeCount: 0, latestEpisodes: [],
    };
    renderGroups([feed]);
    expect(screen.getByRole('heading', { name: 'Show C' })).toBeTruthy();
    expect(screen.getByText('No episodes yet')).toBeTruthy();
  });

  it('group header links to the podcast', () => {
    const feed: Feed = {
      slug: 'show-d', title: 'Show D', sourceUrl: 'https://example.com/d.xml', feedUrl: 'https://example.com/d.xml',
      episodeCount: 0, latestEpisodes: [],
    };
    renderGroups([feed]);
    const heading = screen.getByRole('heading', { name: 'Show D' });
    expect(heading.querySelector('a')?.getAttribute('href')).toBe('/feeds/show-d');
    expect(screen.getByRole('link', { name: 'View all episodes' }).getAttribute('href')).toBe('/feeds/show-d');
  });

  it('disables a queued row action while a sibling row stays actionable', () => {
    const feed: Feed = {
      slug: 'show-e', title: 'Show E', sourceUrl: 'https://example.com/e.xml', feedUrl: 'https://example.com/e.xml',
      episodeCount: 2,
      latestEpisodes: [
        episodeSummary({ id: 'e1', jobState: 'queued', status: 'processing' }),
        episodeSummary({ id: 'e2', jobState: 'idle' }),
      ],
    };
    renderGroups([feed]);
    const queuedButton = screen.getByText('Queued').closest('button') as HTMLButtonElement;
    expect(queuedButton.disabled).toBe(true);
    const idleButton = screen.getByText('Reprocess').closest('button') as HTMLButtonElement;
    expect(idleButton.disabled).toBe(false);
  });
});

describe('clampEpisodesPerPodcast', () => {
  it('clamps to the 1-10 bounded range and falls back on non-finite input', () => {
    expect(clampEpisodesPerPodcast(0)).toBe(1);
    expect(clampEpisodesPerPodcast(25)).toBe(10);
    expect(clampEpisodesPerPodcast(3)).toBe(3);
    expect(clampEpisodesPerPodcast(NaN)).toBe(3);
  });
});
