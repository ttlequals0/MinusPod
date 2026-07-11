import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CueCandidatesSection from './CueCandidatesSection';

vi.mock('../api/cueTemplates', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/cueTemplates')>();
  return {
    ...actual,
    getCueCandidates: vi.fn(),
    dismissCueCandidate: vi.fn(),
    listCueCandidateDismissals: vi.fn(),
    undoCueCandidateDismissal: vi.fn(),
  };
});
vi.mock('../api/settings', () => ({ getSettings: vi.fn().mockResolvedValue({}) }));

import {
  getCueCandidates, dismissCueCandidate,
  listCueCandidateDismissals, undoCueCandidateDismissal,
} from '../api/cueTemplates';

const renderSection = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CueCandidatesSection
        slug="feed" episodeId="ep1" episodeTitle="Ep"
        episodeDuration={600} hasOriginalAudio
      />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.mocked(getCueCandidates).mockResolvedValue({
    episodeId: 'ep1',
    status: 'ready',
    candidates: [
      { start: 10, end: 12, kind: 'recurring', count: 4 },
      { start: 50, end: 52, kind: 'recurring', count: 3, dismissed: true, dismissalId: 9 },
    ],
  });
  vi.mocked(listCueCandidateDismissals).mockResolvedValue([
    { id: 9, label: 'Repeats 3x', sourceEpisodeId: 'ep1', startS: 50, endS: 52 },
  ]);
});

describe('CueCandidatesSection dismissals', () => {
  it('splits active and dismissed candidates', async () => {
    renderSection();
    await userEvent.click(screen.getByRole('button', { name: /find audio cues/i }));
    await waitFor(() => expect(screen.getByText(/Repeats 4x/)).toBeDefined());
    // dismissed candidate is not in the active list
    expect(screen.queryByText(/Repeats 3x -- /)).toBeNull();
    expect(screen.getByRole('button', { name: /dismissed \(1\)/i })).toBeDefined();
  });

  it('dismiss calls the API and refetches', async () => {
    vi.mocked(dismissCueCandidate).mockResolvedValue(
      { id: 11, label: null, sourceEpisodeId: 'ep1', startS: 10, endS: 12 });
    renderSection();
    await userEvent.click(screen.getByRole('button', { name: /find audio cues/i }));
    await waitFor(() => expect(screen.getByText(/Repeats 4x/)).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /^dismiss$/i }));
    await waitFor(() => expect(dismissCueCandidate).toHaveBeenCalledWith(
      'feed', 'ep1', expect.objectContaining({ start_s: 10, end_s: 12 })));
  });

  it('dismiss stops an in-flight preview', async () => {
    // jsdom does not implement HTMLMediaElement playback; stub it so the
    // preview state machine runs.
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
    try {
      vi.mocked(dismissCueCandidate).mockResolvedValue(
        { id: 11, label: null, sourceEpisodeId: 'ep1', startS: 10, endS: 12 });
      renderSection();
      await userEvent.click(screen.getByRole('button', { name: /find audio cues/i }));
      await waitFor(() => expect(screen.getByText(/Repeats 4x/)).toBeDefined());
      await userEvent.click(screen.getByRole('button', { name: /play candidate/i }));
      expect(screen.getByRole('button', { name: /stop preview/i })).toBeDefined();
      pause.mockClear();
      await userEvent.click(screen.getByRole('button', { name: /^dismiss$/i }));
      // stopPreview ran: audio paused and the button is back to "Play candidate".
      expect(pause).toHaveBeenCalled();
      await waitFor(() =>
        expect(screen.queryByRole('button', { name: /stop preview/i })).toBeNull());
    } finally {
      play.mockRestore();
      pause.mockRestore();
    }
  });

  it('undo calls the API', async () => {
    vi.mocked(undoCueCandidateDismissal).mockResolvedValue(undefined);
    renderSection();
    await userEvent.click(screen.getByRole('button', { name: /find audio cues/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: /dismissed \(1\)/i })).toBeDefined());
    await userEvent.click(screen.getByRole('button', { name: /dismissed \(1\)/i }));
    await userEvent.click(screen.getByRole('button', { name: /undo/i }));
    await waitFor(() => expect(undoCueCandidateDismissal).toHaveBeenCalledWith(9));
  });
});
