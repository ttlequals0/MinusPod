import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ReviewTab from './ReviewTab';

const mockAwaiting = vi.fn();
const mockHeld = vi.fn();
const mockApprove = vi.fn();

vi.mock('../../api/settings', async (importActual) => ({
  APPROVAL_INVALIDATE_KEYS: (await importActual<typeof import('../../api/settings')>()).APPROVAL_INVALIDATE_KEYS,
  getAwaitingApproval: (...args: unknown[]) => mockAwaiting(...args),
  getHeldEpisodes: (...args: unknown[]) => mockHeld(...args),
  approveAwaitingEpisodes: (...args: unknown[]) => mockApprove(...args),
}));

const row = (n: number, held = 0) => ({
  slug: 'example-podcast', episodeId: `ep-${n}`, title: `Episode ${n}`, podcast: 'Example Podcast',
  pendingSince: new Date(Date.now() - 3 * 3600000).toISOString(), adCount: n, pendingReviewCount: held,
});

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><ReviewTab /></MemoryRouter></QueryClientProvider>);
}

describe('ReviewTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAwaiting.mockResolvedValue({ count: 3, episodes: [row(1), row(2, 1), row(3)] });
    mockHeld.mockResolvedValue({ count: 1, episodes: [row(4, 2)] });
    mockApprove.mockResolvedValue({ approved: 2, skipped: 0 });
  });

  it('renders both sections as a tabpanel with episode links', async () => {
    renderTab();
    expect(await screen.findByRole('link', { name: 'Episode 1' })).toBeTruthy();
    expect(screen.getByRole('tabpanel').id).toBe('queue-panel-review');
    expect(screen.getByRole('link', { name: 'Episode 1' }).getAttribute('href')).toBe('/feeds/example-podcast/episodes/ep-1');
    expect((await screen.findByRole('link', { name: 'Episode 4' })).getAttribute('href'))
      .toBe('/feeds/example-podcast/episodes/ep-4#held-for-review-section');
    expect(screen.getAllByText('Waiting 3h ago').length).toBe(3);
  });

  it('approves only the selected rows and refetches', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByRole('link', { name: 'Episode 1' });
    const approve = screen.getByRole('button', { name: 'Approve selected (0)' }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    await user.click(screen.getByLabelText('Select Episode 1'));
    await user.click(screen.getByLabelText('Select Episode 3'));
    await user.click(screen.getByRole('button', { name: 'Approve selected (2)' }));
    await waitFor(() => expect(mockApprove).toHaveBeenCalledWith([
      { slug: 'example-podcast', episodeId: 'ep-1' },
      { slug: 'example-podcast', episodeId: 'ep-3' },
    ]));
    await waitFor(() => expect(mockAwaiting).toHaveBeenCalledTimes(2));
  });

  it('Select all selects every row', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByRole('link', { name: 'Episode 1' });
    await user.click(screen.getByLabelText('Select all'));
    expect(screen.getByRole('button', { name: 'Approve selected (3)' })).toBeTruthy();
  });

  it('shows empty states for both sections', async () => {
    mockAwaiting.mockResolvedValue({ count: 0, episodes: [] });
    mockHeld.mockResolvedValue({ count: 0, episodes: [] });
    renderTab();
    expect(await screen.findByText('No episodes are awaiting approval.')).toBeTruthy();
    expect(await screen.findByText('No detections are held for review.')).toBeTruthy();
  });

  it('shows an error with Retry instead of an empty claim', async () => {
    mockAwaiting.mockRejectedValue(new Error('down'));
    renderTab();
    const section = (await screen.findByRole('heading', { name: 'Awaiting approval' })).closest('section') as HTMLElement;
    expect(await within(section).findByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(within(section).queryByText('No episodes are awaiting approval.')).toBeNull();
  });
});
