/**
 * The bar that collects review decisions and applies them in one recut per
 * episode, including what it says when nothing could be recut.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PendingRecutsBar } from './PendingRecutsBar';
import * as settingsApi from '../../api/settings';

vi.mock('../../api/settings', () => ({
  getPendingRecuts: vi.fn(),
  applyPendingRecuts: vi.fn(),
}));

const mocked = vi.mocked(settingsApi);

function renderBar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PendingRecutsBar />
    </QueryClientProvider>,
  );
}

const pending = (count: number) => ({
  count,
  episodes: Array.from({ length: count }, (_, i) => ({
    slug: 'example-podcast', episodeId: `ep-${i}`, title: `Episode ${i}`,
    podcast: 'The Daily Tech Show', pendingSince: '2026-08-30T00:00:00Z',
  })),
});

beforeEach(() => vi.clearAllMocks());

describe('PendingRecutsBar', () => {
  it('stays hidden when nothing is waiting', async () => {
    mocked.getPendingRecuts.mockResolvedValue(pending(0));
    const { container } = renderBar();
    await waitFor(() => expect(mocked.getPendingRecuts).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('names the count and applies in one pass', async () => {
    mocked.getPendingRecuts.mockResolvedValue(pending(2));
    mocked.applyPendingRecuts.mockResolvedValue({ queued: 2, skipped: 0 });
    renderBar();
    const user = userEvent.setup();
    const button = await screen.findByRole('button', { name: 'Apply recuts (2)' });
    expect(screen.getByText(/2 episodes still play their old audio/)).toBeTruthy();
    await user.click(button);
    await waitFor(() => expect(mocked.applyPendingRecuts).toHaveBeenCalledOnce());
    expect(await screen.findByText(/Recutting 2 episodes\./)).toBeTruthy();
  });

  it('says why when nothing could be recut, rather than looking inert', async () => {
    mocked.getPendingRecuts.mockResolvedValue(pending(2));
    mocked.applyPendingRecuts.mockResolvedValue({ queued: 0, skipped: 2 });
    renderBar();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Apply recuts (2)' }));
    expect(
      await screen.findByText(/Nothing could be recut\. 2 episodes are missing the audio or transcript/),
    ).toBeTruthy();
  });

  it('surfaces a failed apply', async () => {
    mocked.getPendingRecuts.mockResolvedValue(pending(1));
    mocked.applyPendingRecuts.mockRejectedValue(new Error('boom'));
    renderBar();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Apply recuts (1)' }));
    expect(await screen.findByText('boom')).toBeTruthy();
  });
});
