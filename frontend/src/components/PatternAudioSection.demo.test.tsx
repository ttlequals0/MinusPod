import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import PatternAudioSection from './PatternAudioSection';
import { DEMO_AUDIO_TOOLTIP } from '../demo/copy';
import type { AdPattern } from '../api/patterns';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));
vi.mock('../api/patterns', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/patterns')>()),
  getPatternAudio: vi.fn(async () => ({
    state: 'active', available: true, length: 45, historyBackfillPending: true,
    builtFrom: { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 10, end: 55, available: true },
    source: { podcastSlug: 'example-podcast', episodeId: 'a1b2c3d4e5f6', start: 10, end: 55 },
    counts: { matches: 1, rejected: 0, outOfScope: 0 }, meanSimilarity: 0.9, lastMatchedAt: null,
  })),
  getPatternAudioMatches: vi.fn(async () => ({
    matches: [{
      id: 1, episodeId: 'e1', podcastSlug: 'example-podcast', episodeTitle: 'Episode 1', start: 10, end: 50,
      similarity: 0.9, coverage: 0.9, storedLength: 45, inScope: true, audioOnly: false, rejected: false, available: true,
      createdAt: '2026-03-04T10:00:00Z',
    }],
    page: 1, perPage: 10, total: 1,
  })),
}));

afterEach(cleanup);

describe('PatternAudioSection in demo', () => {
  test('disables playback and avoids a live backfill notice', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <PatternAudioSection pattern={{ id: 7 } as AdPattern} />
      </QueryClientProvider>,
    );
    const source = await screen.findByRole('button', { name: 'Play source audio' }) as HTMLButtonElement;
    expect(source.disabled).toBe(true);
    expect(source.getAttribute('title')).toBe(DEMO_AUDIO_TOOLTIP);
    expect(screen.getByText('Match history does not update in this demo.')).toBeTruthy();
    expect(screen.queryByText('Match history is still being filled in.')).toBeNull();
    const row = (await screen.findAllByRole('listitem'))[0];
    const match = within(row).getByRole('button', { name: /^Play match/ }) as HTMLButtonElement;
    expect(match.disabled).toBe(true);
    expect(match.getAttribute('title')).toBe(DEMO_AUDIO_TOOLTIP);
  });
});
