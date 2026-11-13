/**
 * Reopening the ad editor lands on the first ad; the selected index used to survive close/reopen (#564).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

vi.mock('../api/feeds', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../api/feeds')>();
  return {
    ...mod,
    getEpisode: vi.fn(),
    getFeed: vi.fn().mockResolvedValue({ slug: 'test-feed', title: 'Feed', artworkUrl: null }),
  };
});
vi.mock('../components/AdEditor', () => ({
  default: ({
    selectedAdIndex,
    onSelectedAdIndexChange,
    onClose,
  }: {
    selectedAdIndex: number;
    onSelectedAdIndexChange: (i: number) => void;
    onClose: () => void;
  }) => (
    <div data-testid="ad-editor" data-selected-index={selectedAdIndex}>
      <button onClick={() => onSelectedAdIndexChange(2)}>select last ad</button>
      <button onClick={onClose}>close editor</button>
    </div>
  ),
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

function renderEpisode() {
  (getEpisode as ReturnType<typeof vi.fn>).mockResolvedValue({
    id: 'ep-1',
    title: 'Test Episode',
    published: '2026-01-01T00:00:00Z',
    status: 'completed',
    transcript: 'transcript.json',
    hasOriginalAudio: true,
    corrections: [],
    pendingReviewMarkers: [],
    adMarkers: [
      { start: 10, end: 40, confidence: 0.9, detection_stage: 'claude' },
      { start: 100, end: 130, confidence: 0.9, detection_stage: 'claude' },
      { start: 200, end: 230, confidence: 0.9, detection_stage: 'claude' },
    ],
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

describe('Ad editor selected index resets on reopen', () => {
  it('reopens on the first ad after ending a session on the last', async () => {
    renderEpisode();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit ads' }));
    expect(screen.getByTestId('ad-editor').getAttribute('data-selected-index')).toBe('0');

    fireEvent.click(screen.getByRole('button', { name: 'select last ad' }));
    expect(screen.getByTestId('ad-editor').getAttribute('data-selected-index')).toBe('2');

    fireEvent.click(screen.getByRole('button', { name: 'close editor' }));
    expect(screen.queryByTestId('ad-editor')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Edit ads' }));
    expect(screen.getByTestId('ad-editor').getAttribute('data-selected-index')).toBe('0');
  });
});
