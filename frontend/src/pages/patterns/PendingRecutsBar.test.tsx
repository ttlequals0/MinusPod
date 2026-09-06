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

const pending = (count: number, overrides: Partial<settingsApi.PendingRecut> = {}) => ({
  count,
  episodes: Array.from({ length: count }, (_, i) => ({
    slug: 'example-podcast', episodeId: `ep-${i}`, title: `Episode ${i}`,
    podcast: 'The Daily Tech Show', pendingSince: '2026-08-30T00:00:00Z',
    recutReady: true, inFlight: false, ...overrides,
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
      await screen.findByText(/Nothing could be recut\. 2 episodes are already queued to run or missing what a recut needs/),
    ).toBeTruthy();
  });

  it('shows in-flight rows as recutting and re-arms for a new decision', async () => {
    // After the apply's refetch the rows report inFlight from the server, so
    // the bar needs no client-side batch tracking.
    mocked.getPendingRecuts.mockResolvedValueOnce(pending(2));
    mocked.getPendingRecuts.mockResolvedValueOnce(
      pending(2, { recutReady: false, inFlight: true }));
    mocked.applyPendingRecuts.mockResolvedValue({ queued: 2, skipped: 0 });
    renderBar();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Apply recuts (2)' }));
    await waitFor(() => {
      const button = screen.getByRole('button') as HTMLButtonElement;
      expect(button.textContent).toBe('Recutting 2...');
      expect(button.disabled).toBe(true);
    });
  });

  it('offers a fresh decision while other rows still run', async () => {
    mocked.getPendingRecuts.mockResolvedValue({
      count: 3,
      episodes: [
        ...pending(2, { recutReady: false, inFlight: true }).episodes,
        ...pending(1, { episodeId: 'ep-new' }).episodes,
      ],
    });
    renderBar();
    const button = await screen.findByRole(
      'button', { name: 'Apply recuts (1)' }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(screen.getByText(/2 of them are being/)).toBeTruthy();
  });

  it('separates rows a recut cannot rebuild and disables an empty apply', async () => {
    mocked.getPendingRecuts.mockResolvedValue(pending(2, { recutReady: false }));
    renderBar();
    const button = await screen.findByRole('button', { name: 'Apply recuts (0)' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/2 of them are missing/)).toBeTruthy();
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
