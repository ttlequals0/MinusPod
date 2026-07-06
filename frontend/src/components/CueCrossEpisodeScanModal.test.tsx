/**
 * Focused tests for CueCrossEpisodeScanModal not covered by the panel suite:
 *   - Escape with a stacked CueMarkModal open must NOT close the parent modal
 *     (the seed modal owns Escape while it is up).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CueCrossEpisodeScanModal from './CueCrossEpisodeScanModal';
import type { Episode } from '../api/types';

// CueMarkModal stub with a stable testid so we can detect the stacked modal.
vi.mock('./CueMarkModal', () => ({
  default: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="cue-mark-modal">
      <button onClick={onClose}>Close modal</button>
    </div>
  ),
}));

const mockCrossEpisodeScan = vi.fn();
vi.mock('../api/cueTemplates', () => ({
  crossEpisodeScan: (...args: unknown[]) => mockCrossEpisodeScan(...args),
}));

const mockGetEpisodes = vi.fn();
const mockGetEpisode = vi.fn();
vi.mock('../api/feeds', () => ({
  getEpisodes: (...args: unknown[]) => mockGetEpisodes(...args),
  getEpisode: (...args: unknown[]) => mockGetEpisode(...args),
}));

function makeEpisode(id: string, title: string): Episode {
  return {
    id,
    title,
    published: '2026-01-01T00:00:00Z',
    status: 'completed',
    duration: 3600,
    hasOriginalAudio: true,
  };
}

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderModal(onClose: () => void) {
  return render(
    <QueryClientProvider client={makeClient()}>
      <CueCrossEpisodeScanModal
        slug="test-feed"
        captureMinSeconds={0.2}
        captureMaxSeconds={2}
        captureMaxIntroSeconds={30}
        captureMaxOutroSeconds={30}
        onClose={onClose}
        onSaved={() => {}}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetEpisodes.mockResolvedValue({
    episodes: [makeEpisode('ep-1', 'Episode 1'), makeEpisode('ep-2', 'Episode 2')],
    total: 2,
  });
  mockCrossEpisodeScan.mockResolvedValue({
    status: 'ready',
    targetEpisodeId: 'ep-1',
    episodeIds: ['ep-1', 'ep-2'],
    candidates: [{ start: 5, end: 8, kind: 'recurring', episodeMatches: 1 }],
  });
});

describe('Escape handling with a stacked CueMarkModal', () => {
  it('does not call onClose when Escape is pressed while a seed modal is open', async () => {
    const onClose = vi.fn();
    renderModal(onClose);

    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());
    await userEvent.click(screen.getByLabelText('Select episode Episode 1'));
    await userEvent.click(screen.getByLabelText('Select episode Episode 2'));
    await userEvent.click(screen.getByRole('button', { name: /^Scan$/ }));

    await waitFor(() => expect(screen.getByRole('button', { name: /Make template/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /Make template/i }));
    await waitFor(() => expect(screen.getByTestId('cue-mark-modal')).toBeDefined());

    await userEvent.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('calls onClose on Escape when no seed modal is open', async () => {
    const onClose = vi.fn();
    renderModal(onClose);

    await waitFor(() => expect(screen.getByText('Episode 1')).toBeDefined());
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
