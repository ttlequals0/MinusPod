/**
 * Episode page ad list: the row keeps showing the reviewed span and offers a
 * result line (with Recut now) after a confirm or reject on a cut row.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

vi.mock('../api/patterns', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api/patterns')>();
  return { ...mod, submitCorrection: vi.fn().mockResolvedValue({}) };
});
vi.mock('../api/feeds', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api/feeds')>();
  return {
    ...mod,
    reprocessEpisode: vi.fn().mockResolvedValue({}),
    getEpisode: vi.fn(),
    getFeed: vi.fn().mockResolvedValue({ slug: 'test-feed', title: 'Feed', artworkUrl: null }),
  };
});
vi.mock('../components/AdEditor', () => ({
  default: ({ detectedAds, onCorrection }: {
    detectedAds: Array<{ start: number; end: number; confidence: number; reason: string }>;
    onCorrection: (c: unknown) => void;
  }) => (
    <div data-testid="ad-editor">
      <button onClick={() => onCorrection({ type: 'reject', originalAd: detectedAds[0] })}>mock reject</button>
      <button onClick={() => onCorrection({ type: 'confirm', originalAd: detectedAds[0] })}>mock confirm</button>
    </div>
  ),
}));
vi.mock('../components/CueDetectionsSection', () => ({ default: () => null }));
vi.mock('../components/CueCandidatesSection', () => ({ default: () => null }));
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
import { getEpisode, reprocessEpisode } from '../api/feeds';

function renderWith(adMarker: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  (getEpisode as ReturnType<typeof vi.fn>).mockResolvedValue({
    id: 'ep-1',
    title: 'Test Episode',
    published: '2026-01-01T00:00:00Z',
    status: 'completed',
    hasOriginalAudio: true,
    corrections: [],
    pendingReviewMarkers: [],
    adMarkers: [adMarker],
    ...extra,
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

describe('ad row range', () => {
  it('shows the reviewed span in the row and the cut range as a secondary line', async () => {
    renderWith({
      start: 588.4, end: 658.6, confidence: 0.9, detection_stage: 'claude',
      reviewer_verdict: 'adjust', reviewer_original_start: 588, reviewer_original_end: 600,
    });
    expect(await screen.findByText('9:48 - 10:00')).toBeDefined();
    expect(screen.getByText('Reviewer: 9:48 - 10:58')).toBeDefined();
  });

  it('shows the plain range for a marker the reviewer did not adjust', async () => {
    renderWith({ start: 588, end: 600, confidence: 0.9, detection_stage: 'claude' });
    expect(await screen.findByText('9:48 - 10:00')).toBeDefined();
    expect(screen.queryByText(/^Reviewer:/)).toBeNull();
  });
});

describe('result line after confirm or reject on a cut row', () => {
  const marker = { start: 10, end: 40, confidence: 0.9, detection_stage: 'claude' };

  it('says the audio comes back at the next recut and offers Recut now', async () => {
    renderWith(marker, {
      corrections: [{ id: 1, correction_type: 'false_positive', original_bounds: { start: 10, end: 40 }, created_at: '2026-01-01' }],
    });
    expect(await screen.findByText('Marked not an ad: the audio comes back at the next recut')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: 'Recut now' }));
    await waitFor(() => expect(reprocessEpisode).toHaveBeenCalledWith('test-feed', 'ep-1', 'recut'));
  });

  it('says Confirmed on a confirmed row, with no recut button', async () => {
    renderWith(marker, {
      corrections: [{ id: 1, correction_type: 'confirm', original_bounds: { start: 10, end: 40 }, created_at: '2026-01-01' }],
    });
    expect(await screen.findByTestId('ad-result-0')).toHaveProperty('textContent', 'Confirmed');
    expect(screen.queryByRole('button', { name: 'Recut now' })).toBeNull();
  });

  it('shows no result line on a row nobody touched', async () => {
    renderWith(marker);
    await screen.findByText('0:10 - 0:40');
    expect(screen.queryByTestId('ad-result-0')).toBeNull();
  });
});
