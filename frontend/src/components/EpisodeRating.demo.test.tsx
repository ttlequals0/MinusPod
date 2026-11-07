import { afterEach, describe, expect, test, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
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
  test('disables Save and Clear with the write tooltip', () => {
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

  test('clears a feed link rating request without rating', async () => {
    const handled = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EpisodeRating slug="show" episodeId="ep-1" requestedRating={4} onRequestedRatingHandled={handled} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(handled).toHaveBeenCalled());
    await act(() => new Promise((r) => setTimeout(r, 0)));
    expect(rateMock).not.toHaveBeenCalled();
  });
});
