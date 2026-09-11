import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import UnresolvedCorrectionsPanel from './UnresolvedCorrectionsPanel';

const mockGet = vi.fn();
const mockAssign = vi.fn();

vi.mock('../../api/patterns', () => ({
  getUnresolvedCorrections: () => mockGet(),
  assignUnresolvedCorrection: (...args: unknown[]) => mockAssign(...args),
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
        { slug: 'feed-a', podcast_title: 'Feed A', episode_title: 'Episode one' },
        { slug: 'feed-b', podcast_title: 'Feed B', episode_title: 'Episode one' },
      ],
    }],
  });
  mockAssign.mockResolvedValue(undefined);
});

describe('UnresolvedCorrectionsPanel', () => {
  it('shows the count and candidate feeds', async () => {
    renderPanel();

    expect(await screen.findByText('Unassigned corrections')).toBeDefined();
    expect(screen.getByText('1')).toBeDefined();
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
    const assign = await screen.findByRole('button', { name: 'Assign correction' });
    expect((assign as HTMLButtonElement).disabled).toBe(true);

    await userEvent.click(screen.getByRole('radio', { name: /Feed B/ }));
    expect((assign as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole('checkbox'));
    expect((assign as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(assign);

    await waitFor(() => expect(mockAssign).toHaveBeenCalledWith(17, 'feed-b'));
  });
});
