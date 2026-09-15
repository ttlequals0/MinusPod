import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import TranscriptSegmentWorkspace from './TranscriptSegmentWorkspace';
import type { EpisodeDetail } from '../api/types';

vi.mock('../hooks/useEpisodeRecutWatch', () => ({
  useEpisodeRecutWatch: () => ({
    phase: 'idle',
    stageLabel: null,
    progress: 0,
    watching: false,
    startWatching: vi.fn(),
    stopWatching: vi.fn(),
    dismissCompletion: vi.fn(),
  }),
}));

vi.mock('../api/feeds', () => ({
  getOriginalSegments: vi.fn(),
  episodeOriginalUrl: (slug: string, id: string) => `/api/v1/feeds/${slug}/episodes/${id}/original.mp3`,
}));

import { getOriginalSegments } from '../api/feeds';

const mockGetOriginalSegments = vi.mocked(getOriginalSegments);

function renderWorkspace(props: Partial<React.ComponentProps<typeof TranscriptSegmentWorkspace>> = {}) {
  const onSubmitCorrection = vi.fn().mockResolvedValue(undefined);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const episode: EpisodeDetail = {
    id: 'ep-1',
    title: 'Test episode',
    status: 'completed',
    published: '2026-01-01T00:00:00Z',
    hasOriginalAudio: true,
    originalTranscriptAvailable: true,
    adMarkers: [{ start: 5, end: 12, confidence: 0.9, reason: 'Squarespace' }],
    pendingReviewMarkers: [],
    rejectedAdMarkers: [],
    keptMarkers: [],
    corrections: [],
    appliedCuts: [{ start: 5, end: 12 }],
  };
  const utils = render(
    <QueryClientProvider client={qc}>
      <TranscriptSegmentWorkspace
        slug="test-feed"
        episodeId="ep-1"
        episode={episode}
        onClose={vi.fn()}
        onSubmitCorrection={onSubmitCorrection}
        onRecut={vi.fn().mockResolvedValue(undefined)}
        onOpenWaveform={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onSubmitCorrection, episode };
}

describe('TranscriptSegmentWorkspace', () => {
  beforeEach(() => {
    mockGetOriginalSegments.mockResolvedValue({
      episodeId: 'ep-1',
      segments: [
        { start: 0, end: 5, text: 'Intro content here for the episode start.' },
        { start: 5, end: 12, text: 'Squarespace dot com slash show promo here today for you.' },
        { start: 12, end: 20, text: 'Back to the show after the break continues now.' },
      ],
    });
  });

  it('renders segment rows and mark actions', async () => {
    renderWorkspace();

    await screen.findByText(/Review transcript/i);
    await screen.findByText(/Squarespace dot com/i);
    screen.getByRole('button', { name: /Mark content/i });
    screen.getByRole('button', { name: /Mark ad/i });
  });

  it('submits reject correction when marking content on selected row', async () => {
    const user = userEvent.setup();
    const { onSubmitCorrection } = renderWorkspace();

    await screen.findByText(/Squarespace dot com/i);
    const checkbox = screen.getByRole('checkbox', { name: /Select segment 2/i });
    await user.click(checkbox);
    await user.click(screen.getByRole('button', { name: /Mark content/i }));

    await waitFor(() => {
      expect(onSubmitCorrection).toHaveBeenCalledWith(expect.objectContaining({
        type: 'reject',
        originalAd: expect.objectContaining({ start: 5, end: 12 }),
      }));
    });
  });

  it('requires sponsor before marking ad', async () => {
    const user = userEvent.setup();
    const { onSubmitCorrection } = renderWorkspace();

    await screen.findByText(/Squarespace dot com/i);
    await user.click(screen.getByRole('checkbox', { name: /Select segment 2/i }));
    await user.click(screen.getByRole('button', { name: /Mark ad/i }));

    await screen.findByText(/Enter a sponsor name/i);
    expect(onSubmitCorrection).not.toHaveBeenCalled();
  });

  it('creates one missed-ad correction per contiguous span', async () => {
    const user = userEvent.setup();
    mockGetOriginalSegments.mockResolvedValue({
      episodeId: 'ep-1',
      segments: [
        {
          start: 0,
          end: 5,
          text: 'First missed ad read with enough words to pass the template minimum.',
        },
        {
          start: 5,
          end: 10,
          text: 'First span continues with more promotional copy for the listeners.',
        },
        {
          start: 12.5,
          end: 18,
          text: 'Second missed ad read with enough words to pass the template minimum.',
        },
      ],
    });
    const { onSubmitCorrection } = renderWorkspace({
      episode: {
        id: 'ep-1',
        title: 'Test episode',
        status: 'completed',
        published: '2026-01-01T00:00:00Z',
        hasOriginalAudio: true,
        originalTranscriptAvailable: true,
        adMarkers: [],
        pendingReviewMarkers: [],
        rejectedAdMarkers: [],
        keptMarkers: [],
        corrections: [],
        appliedCuts: [],
      },
    });

    await screen.findByText(/First missed ad/i);
    await user.click(screen.getByRole('checkbox', { name: /Select segment 1/i }));
    await user.click(screen.getByRole('checkbox', { name: /Select segment 2/i }));
    await user.click(screen.getByRole('checkbox', { name: /Select segment 3/i }));
    await screen.findByText(/2 spans · 3 segments selected/i);

    await user.type(screen.getByPlaceholderText(/e\.g\. Squarespace/i), 'Acme');
    await user.click(screen.getByRole('button', { name: /Mark ad/i }));

    await waitFor(() => {
      expect(onSubmitCorrection).toHaveBeenCalledTimes(2);
    });
    expect(onSubmitCorrection).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({
        type: 'create',
        start: 0,
        end: 10,
        sponsor: 'Acme',
      }),
    );
    expect(onSubmitCorrection).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({
        type: 'create',
        start: 12.5,
        end: 18,
        sponsor: 'Acme',
      }),
    );
    await screen.findByText(/Saved 2 missed ads/i);
  });

  it('rejects multi-span mark ad when any span text is too short', async () => {
    const user = userEvent.setup();
    mockGetOriginalSegments.mockResolvedValue({
      episodeId: 'ep-1',
      segments: [
        {
          start: 0,
          end: 5,
          text: 'Long enough first span text that clearly exceeds fifty characters easily.',
        },
        {
          start: 8,
          end: 9,
          text: 'Hi.',
        },
      ],
    });
    const { onSubmitCorrection } = renderWorkspace({
      episode: {
        id: 'ep-1',
        title: 'Test episode',
        status: 'completed',
        published: '2026-01-01T00:00:00Z',
        hasOriginalAudio: true,
        originalTranscriptAvailable: true,
        adMarkers: [],
        pendingReviewMarkers: [],
        rejectedAdMarkers: [],
        keptMarkers: [],
        corrections: [],
        appliedCuts: [],
      },
    });

    await screen.findByText(/Long enough first span/i);
    await user.click(screen.getByRole('checkbox', { name: /Select segment 1/i }));
    await user.click(screen.getByRole('checkbox', { name: /Select segment 2/i }));
    await user.type(screen.getByPlaceholderText(/e\.g\. Squarespace/i), 'Acme');
    await user.click(screen.getByRole('button', { name: /Mark ad/i }));

    await screen.findByText(/Selected text is too short/i);
    expect(onSubmitCorrection).not.toHaveBeenCalled();
  });
});
