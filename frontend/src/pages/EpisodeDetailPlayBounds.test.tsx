/**
 * Audition bounds: the play button plays the span the row displays, from original audio. Plain markers use
 * start/end; reviewer-adjusted markers use reviewer_original_start/end. The audition hook is mocked to
 * capture toggle() args.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

const toggleSpy = vi.fn();
vi.mock('../hooks/useAuditionPlayer', () => ({
  useAuditionPlayer: () => ({
    playingKey: null,
    toggle: toggleSpy,
    stop: vi.fn(),
    audioElement: <audio data-testid="mock-audition-audio" />,
  }),
}));

vi.mock('../api/feeds', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api/feeds')>();
  return {
    ...mod,
    getEpisode: vi.fn(),
    getFeed: vi.fn().mockResolvedValue({ slug: 'test-feed', title: 'Feed', artworkUrl: null }),
  };
});
vi.mock('../components/AdEditor', () => ({
  default: () => <div data-testid="ad-editor" />,
}));
vi.mock('../components/CueDetectionsSection', () => ({
  default: () => null,
}));
vi.mock('../components/CueCandidatesSection', () => ({
  default: () => null,
}));
vi.mock('react-router', async (importOriginal) => {
  const mod = await importOriginal<typeof import('react-router')>();
  return {
    ...mod,
    useParams: () => ({ slug: 'test-feed', episodeId: 'ep-1' }),
    useSearchParams: () => [new URLSearchParams(), () => {}],
    Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  };
});

import EpisodeDetail from './EpisodeDetail';
import { getEpisode } from '../api/feeds';

function renderWith(adMarker: Record<string, unknown>) {
  (getEpisode as ReturnType<typeof vi.fn>).mockResolvedValue({
    id: 'ep-1',
    title: 'Test Episode',
    published: '2026-01-01T00:00:00Z',
    status: 'completed',
    hasOriginalAudio: true,
    corrections: [],
    pendingReviewMarkers: [],
    adMarkers: [adMarker],
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EpisodeDetail />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  toggleSpy.mockClear();
});

describe('Detected ads: audition plays the displayed timeframe', () => {
  it('plays start/end for a plain marker', async () => {
    renderWith({ start: 10, end: 40, confidence: 0.9, detection_stage: 'claude' });
    fireEvent.click(await screen.findByRole('button', { name: 'Play this ad' }));
    expect(toggleSpy).toHaveBeenCalledTimes(1);
    const [, , start, end] = toggleSpy.mock.calls[0];
    expect(start).toBe(10);
    expect(end).toBe(40);
  });

  it('plays the reviewed pre-trim span for an adjusted marker', async () => {
    renderWith({
      start: 3895.8, end: 3929.9, confidence: 0.95, detection_stage: 'dai_differential',
      reviewer_verdict: 'adjust',
      reviewer_original_start: 3872.9, reviewer_original_end: 3933.3,
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Play this ad' }));
    const [, , start, end] = toggleSpy.mock.calls[0];
    expect(start).toBe(3872.9);
    expect(end).toBe(3933.3);
  });
});
