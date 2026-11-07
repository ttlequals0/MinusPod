import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import EpisodeRating from './EpisodeRating';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';
import { rateEpisode } from '../api/feeds';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));
vi.mock('../api/feeds', () => ({
  rateEpisode: vi.fn(),
  clearEpisodeRating: vi.fn(),
}));

const rateMock = vi.mocked(rateEpisode);

afterEach(cleanup);

describe('EpisodeRating in demo', () => {
  it('disables Save and Clear with the write tooltip', () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EpisodeRating slug="show" episodeId="ep-1" rating={{ value: 3, notes: null, ratedAt: '2026-09-01T00:00:00Z' }} />
      </QueryClientProvider>,
    );
    for (const name of ['Save', 'Clear']) {
      const button = screen.getByRole('button', { name }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      expect(button.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);
    }
  });

  it('does not rate on mount when a feed link requests a rating', () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EpisodeRating slug="show" episodeId="ep-1" requestedRating={4} />
      </QueryClientProvider>,
    );
    expect(rateMock).not.toHaveBeenCalled();
  });
});
