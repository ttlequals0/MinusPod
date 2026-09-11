import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import UnresolvedCorrectionsPanel from './UnresolvedCorrectionsPanel';

const mockGet = vi.fn();
const mockAssign = vi.fn();
const mockDelete = vi.fn();

vi.mock('../../api/patterns', () => ({
  getUnresolvedCorrections: () => mockGet(),
  assignUnresolvedCorrection: (...args: unknown[]) => mockAssign(...args),
  deleteUnresolvedCorrection: (...args: unknown[]) => mockDelete(...args),
}));

function renderPanel() {
  return render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })}>
      <UnresolvedCorrectionsPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue({
    count: 1,
    corrections: [{
      id: 17,
      episode_id: 'episode-1',
      podcast_title: 'Legacy feed',
      episode_title: 'Episode one',
      correction_type: 'false_positive',
      created_at: '2026-01-01T00:00:00Z',
      original_bounds: { start: 1, end: 2 },
      corrected_bounds: null,
      candidates: [
        { slug: 'feed-a', podcast_title: 'Feed A', episode_title: 'Episode one', episode_available: true, source: 'current', history_run_count: null, history_latest_processed_at: null },
        { slug: 'feed-b', podcast_title: 'Feed B', episode_title: 'Episode one', episode_available: true, source: 'current', history_run_count: null, history_latest_processed_at: null },
      ],
    }],
  });
  mockAssign.mockResolvedValue(undefined);
  mockDelete.mockResolvedValue(undefined);
});

describe('UnresolvedCorrectionsPanel', () => {
  it('shows the count and candidate feeds', async () => {
    renderPanel();

    expect(await screen.findByText('Unassigned corrections')).toBeDefined();
    expect(screen.getByText('1')).toBeDefined();
    expect(screen.getByRole('button', { name: /Unassigned corrections/ }).getAttribute('aria-expanded')).toBe('false');
    await userEvent.click(screen.getByRole('button', { name: /Unassigned corrections/ }));
    expect(screen.getByText('Feed A')).toBeDefined();
    expect(screen.getByText('Feed B')).toBeDefined();
    expect(screen.getByText('Saved under Legacy feed')).toBeDefined();
    expect(screen.getByText('Original segment: 1.0 s to 2.0 s')).toBeDefined();
    expect(screen.getAllByRole('link', { name: 'Review episode' })[1].getAttribute('href')).toBe(
      '/ui/feeds/feed-b/episodes/episode-1',
    );
  });

  it('requires a candidate and explicit confirmation before assignment', async () => {
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Unassigned corrections/ }));
    const assign = await screen.findByRole('button', { name: 'Assign correction' });
    expect((assign as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(screen.getByRole('radio', { name: /Feed B/ }));
    expect((assign as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole('checkbox'));
    expect((assign as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(assign);

    await waitFor(() => expect(mockAssign).toHaveBeenCalledWith(17, 'feed-b'));
  });

  it('keeps assignment unavailable when no candidate feed exists', async () => {
    mockGet.mockResolvedValueOnce({
      count: 1,
      corrections: [{
        id: 17, episode_id: 'episode-1', podcast_title: null, episode_title: null,
        correction_type: 'false_positive', created_at: '2026-01-01T00:00:00Z',
        original_bounds: null, corrected_bounds: null, candidates: [],
      }],
    });
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Unassigned corrections/ }));
    expect(await screen.findByText('No matching feed is available.')).toBeDefined();
    expect((screen.getByRole('button', { name: 'Assign correction' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('requires historical-record confirmation without offering an unavailable review link', async () => {
    mockGet.mockResolvedValueOnce({
      count: 1,
      corrections: [{
        id: 17, episode_id: 'episode-1', podcast_title: null, episode_title: null,
        correction_type: 'false_positive', created_at: '2026-01-01T00:00:00Z',
        original_bounds: null, corrected_bounds: null,
        candidates: [{ slug: 'history-feed', podcast_title: 'History Feed', episode_title: 'Archived episode', episode_available: false, source: 'history', history_run_count: 1, history_latest_processed_at: '2026-09-10T00:00:00Z' }],
      }],
    });
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Unassigned corrections/ }));
    await userEvent.click(await screen.findByRole('radio', { name: /History Feed/ }));
    expect(screen.getByText('Historical record')).toBeDefined();
    expect(screen.getByText('Episode review is unavailable because the current episode is gone.')).toBeDefined();
    expect(screen.getByText('1 processing history entry')).toBeDefined();
    expect(screen.queryByRole('link', { name: 'Review episode' })).toBeNull();
    expect(screen.getByText('I reviewed the historical record and confirm this feed.')).toBeDefined();
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button', { name: 'Assign correction' }));
    await waitFor(() => expect(mockAssign).toHaveBeenCalledWith(17, 'history-feed'));
  });

  it('confirms before permanently deleting an unassigned correction record', async () => {
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Unassigned corrections/ }));
    await userEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('does not change source patterns, media, or feeds');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(17));
  });

  it('keeps the correction when deletion is cancelled or rejected', async () => {
    mockDelete.mockRejectedValueOnce(new Error('Unable to delete correction'));
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: /Unassigned corrections/ }));
    await userEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    let dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(mockDelete).not.toHaveBeenCalled();
    expect(screen.getByText('Correction #17')).toBeDefined();
    await userEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
    dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Unable to delete correction')).toBeDefined();
    expect(screen.getByText('Correction #17')).toBeDefined();
  });
});
