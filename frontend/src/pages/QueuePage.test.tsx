import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import QueuePage from './QueuePage';

const mockGetEpisodes = vi.fn();
const mockGetSettings = vi.fn();
const mockGetAdmission = vi.fn();
const mockCancel = vi.fn();
const mockSetPriority = vi.fn();
const mockUpdateSettings = vi.fn();
const mockAwaiting = vi.fn();
const mockHeld = vi.fn();

vi.mock('../api/settings', () => ({
  getProcessingEpisodes: (...args: unknown[]) => mockGetEpisodes(...args),
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
  getProcessingAdmission: (...args: unknown[]) => mockGetAdmission(...args),
  cancelProcessing: (...args: unknown[]) => mockCancel(...args),
  setQueuePriority: (...args: unknown[]) => mockSetPriority(...args),
  updateSettings: (...args: unknown[]) => mockUpdateSettings(...args),
  getAwaitingApproval: (...args: unknown[]) => mockAwaiting(...args),
  getHeldEpisodes: (...args: unknown[]) => mockHeld(...args),
  approveAwaitingEpisodes: vi.fn(),
  setProcessingAdmission: vi.fn(),
  getOfflineQueueSettings: vi.fn(),
  getProviderBudget: vi.fn(),
  getProviderBudgetCurrencies: vi.fn(),
  getProviderBudgetRate: vi.fn(),
  updateOfflineQueueSettings: vi.fn(),
  updateProviderBudget: vi.fn(),
  getRateLimitHoldSettings: vi.fn(),
  updateRateLimitHoldSettings: vi.fn(),
  resetRateLimitHold: vi.fn(),
  getProcessingSchedule: vi.fn(),
  updateProcessingSchedule: vi.fn(),
}));

function settings() {
  return {
    processNewEpisodesFirst: { value: true, isDefault: false },
    queueManualBoost: { value: 20, isDefault: false },
    queueFreshBoost: { value: 5, isDefault: false },
    queueBulkBoost: { value: 0, isDefault: false },
    defaults: { processNewEpisodesFirst: true },
  };
}

function episode(position: number, stage = 'queued') {
  return {
    episodeId: `episode-${position}`,
    slug: 'example-podcast',
    title: `Episode ${position}`,
    podcast: 'Example Podcast',
    startedAt: null,
    stage,
    queuePosition: position,
    queueTotal: 26,
    priority: 0,
  };
}

function renderPage(initial = '/queue') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <QueuePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

describe('QueuePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem('queue-page-control');
    localStorage.removeItem('queue-page-episodes');
    mockGetSettings.mockResolvedValue(settings());
    mockGetAdmission.mockResolvedValue({ paused: false, activeRuns: 0, queuedEpisodes: 26 });
    mockCancel.mockResolvedValue({ message: 'Canceled' });
    mockSetPriority.mockResolvedValue({ message: 'Updated', priority: 0 });
    mockUpdateSettings.mockResolvedValue({ message: 'Updated' });
  });

  it('shows loading and API error states for the queue rows', async () => {
    mockGetEpisodes.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByRole('heading', { name: 'Queue' })).toBeDefined();
    expect(screen.getByText('Episodes currently moving through the pipeline.')).toBeDefined();

    mockGetEpisodes.mockRejectedValueOnce(new Error('queue unavailable'));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['processing-episodes', 1], undefined);
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter><QueuePage /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Could not load queued episodes.')).toBeDefined();
  });

  it('keeps active cancellation available and pages the complete waiting list', async () => {
    mockGetEpisodes.mockImplementation(({ queueOffset }: { queueOffset?: number }) => Promise.resolve(
      queueOffset ? [episode(26)] : [{ ...episode(0, 'transcribing'), queuePosition: undefined }, episode(1)],
    ));
    const client = renderPage();
    client.setQueryData(['processing-status'], {
      currentJob: { slug: 'example-podcast', episodeId: 'episode-0', title: 'Episode 0', podcastName: 'Example Podcast', stage: 'transcribing', progress: 25, elapsed: 4 },
      queueLength: 25,
    });
    await screen.findByText('Episode 0');
    expect(screen.getByRole('heading', { name: 'Queue' }).closest('.max-w-7xl')).not.toBeNull();
    const activeLink = screen.getByRole('link', { name: 'Episode 0' });
    expect(activeLink.getAttribute('href')).toBe('/feeds/example-podcast/episodes/episode-0');
    expect(screen.getByText('Transcribing')).toBeDefined();
    expect(screen.getByText(/25% - \d+s/)).toBeDefined();
    expect(screen.getByRole('progressbar', { name: 'Transcribing progress' }).getAttribute('aria-valuenow')).toBe('25');
    expect(screen.getAllByRole('button', { name: 'Cancel' })[0].className).toContain('px-3');
    await userEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]);
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith('example-podcast', 'episode-0'));
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(mockGetEpisodes).toHaveBeenCalledWith({ queueOffset: 25, queueLimit: 25 }));
  });

  it('commits queue numeric controls on blur after the draft is complete', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue control' }));
    const input = screen.getByLabelText('Play / Reprocess');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '30' } });
    expect(mockUpdateSettings).not.toHaveBeenCalled();
    fireEvent.blur(input);
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith({ queueManualBoost: 30 }));
  });

  it('renders Queue priority as an h3 sub-heading like its sibling groups', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    mockGetSettings.mockResolvedValue(settings());
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue control' }));
    const heading = screen.getByRole('heading', { level: 3, name: 'Queue priority' });
    expect(heading.className).toBe(screen.getByRole('heading', { level: 3, name: 'Processing order' }).className);
  });

  it('shows the saved processing order and saves a change', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    mockGetSettings.mockResolvedValue({ ...settings(), queueOrder: { value: 'oldest_first', isDefault: false } });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue control' }));
    const select = screen.getByRole('combobox', { name: 'Processing order' }) as HTMLSelectElement;
    expect(select.value).toBe('oldest_first');
    expect(select.getAttribute('aria-describedby')).toBe('queueOrder-help');
    expect(screen.getByText('Applies within each feed. Priorities still come first.')).toBeDefined();
    await userEvent.selectOptions(select, 'newest_first');
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith({ queueOrder: 'newest_first' }));
  });

  it('refetches the queue list after the global order changes', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue control' }));
    const calls = mockGetEpisodes.mock.calls.length;
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Processing order' }), 'newest_first');
    await waitFor(() => expect(mockGetEpisodes.mock.calls.length).toBeGreaterThan(calls));
  });

  it('shows the save error when the server rejects the order', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    mockUpdateSettings.mockRejectedValue(new Error('invalid queueOrder'));
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue control' }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Processing order' }), 'oldest_first');
    expect(await screen.findByText('Could not save queue controls. Try again.')).toBeDefined();
  });

  it('renders As queued when the settings omit queueOrder', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue control' }));
    expect((screen.getByRole('combobox', { name: 'Processing order' }) as HTMLSelectElement).value).toBe('queued');
  });

  it('does not describe an active-only queue as having no processing', async () => {
    mockGetAdmission.mockResolvedValue({ paused: false, activeRuns: 1, queuedEpisodes: 0 });
    mockGetEpisodes.mockResolvedValue([
      { ...episode(0, 'transcribing'), queuePosition: undefined },
    ]);
    renderPage();

    await screen.findByRole('link', { name: 'Episode 0' });
    expect(screen.getByText('1 active run and 0 queued episodes.')).toBeDefined();
    expect(screen.getByText('No queued episodes')).toBeDefined();
    expect(screen.queryByText('No episodes processing or queued')).toBeNull();
  });

  it('shows the Queue tab by default and switches to Episode review', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    mockAwaiting.mockResolvedValue({ count: 1, episodes: [{ slug: 's', episodeId: 'e1', title: 'Waiting one', podcast: 'P', pendingSince: new Date().toISOString(), adCount: 1, pendingReviewCount: 0 }] });
    mockHeld.mockResolvedValue({ count: 0, episodes: [] });
    renderPage();
    expect(screen.getByRole('tab', { name: 'Queue' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').id).toBe('queue-panel-queue');
    expect(screen.queryByText('Awaiting approval')).toBeNull();
    await userEvent.click(screen.getByRole('tab', { name: 'Episode review' }));
    expect((await screen.findAllByRole('link', { name: 'Waiting one' })).length).toBe(2);
    expect(screen.getByRole('tab', { name: 'Episode review' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').id).toBe('queue-panel-review');
    expect(screen.queryByText('Episodes currently moving through the pipeline.')).toBeNull();
  });

  it('opens the Episode review tab from ?tab=review', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    mockAwaiting.mockResolvedValue({ count: 0, episodes: [] });
    mockHeld.mockResolvedValue({ count: 0, episodes: [] });
    renderPage('/queue?tab=review');
    expect(screen.getByRole('tab', { name: 'Episode review' }).getAttribute('aria-selected')).toBe('true');
    expect(await screen.findByText('No episodes are awaiting approval.')).toBeTruthy();
  });
});

describe('QueuePage page in the URL', () => {
  beforeEach(() => {
    mockGetSettings.mockResolvedValue(settings());
    mockGetAdmission.mockResolvedValue({ paused: false, activeRuns: 0, queuedEpisodes: 26 });
    mockGetEpisodes.mockResolvedValue([episode(26)]);
  });

  it('requests the queue offset named by ?page=2', async () => {
    renderPage('/queue?page=2');
    await waitFor(() => expect(mockGetEpisodes).toHaveBeenCalledWith({ queueOffset: 25, queueLimit: 25 }));
  });

  it('explains a closed processing schedule in the queue hold box', async () => {
    mockGetEpisodes.mockResolvedValue([episode(1)]);
    const client = renderPage();
    client.setQueryData(['processing-status'], {
      currentJob: null,
      queueLength: 1,
      processingSchedule: { enabled: true, open: false, nextChangeAt: '2026-11-10T22:00:00Z' },
    });
    await screen.findAllByText('Episode 1');
    expect(screen.getByText('Queue hold')).toBeDefined();
    expect(screen.getByText(/Processing is outside its schedule/).textContent).toMatch(/until .+\d/);
  });

  it('shows no schedule hold when the window is closed and nothing is queued', async () => {
    mockGetEpisodes.mockResolvedValue([episode(1)]);
    const client = renderPage();
    client.setQueryData(['processing-status'], {
      currentJob: null,
      queueLength: 0,
      processingSchedule: { enabled: true, open: false, nextChangeAt: '2026-11-10T22:00:00Z' },
    });
    await screen.findAllByText('Episode 1');
    expect(screen.queryByText('Queue hold')).toBeNull();
  });

  it('shows no schedule hold while the window is open or the schedule is off', async () => {
    mockGetEpisodes.mockResolvedValue([episode(1)]);
    const client = renderPage();
    client.setQueryData(['processing-status'], {
      currentJob: null,
      queueLength: 1,
      processingSchedule: { enabled: true, open: true, nextChangeAt: '2026-11-10T22:00:00Z' },
    });
    await screen.findAllByText('Episode 1');
    expect(screen.queryByText('Queue hold')).toBeNull();
  });
});
