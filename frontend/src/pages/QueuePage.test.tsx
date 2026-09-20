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

vi.mock('../api/settings', () => ({
  getProcessingEpisodes: (...args: unknown[]) => mockGetEpisodes(...args),
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
  getProcessingAdmission: (...args: unknown[]) => mockGetAdmission(...args),
  cancelProcessing: (...args: unknown[]) => mockCancel(...args),
  setQueuePriority: (...args: unknown[]) => mockSetPriority(...args),
  updateSettings: (...args: unknown[]) => mockUpdateSettings(...args),
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

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
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
    expect(screen.getByText('Queue')).toBeDefined();
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
    expect(screen.getByRole('heading', { name: 'Queue' }).parentElement?.parentElement?.className).toContain('max-w-7xl');
    const activeLink = screen.getByRole('link', { name: 'Episode 0' });
    expect(activeLink.getAttribute('href')).toBe('/feeds/example-podcast/episodes/episode-0');
    expect(screen.getByText('Transcribing')).toBeDefined();
    expect(screen.getByText(/25% - \d+s/)).toBeDefined();
    expect(screen.getByRole('progressbar', { name: 'Transcribing progress' }).getAttribute('aria-valuenow')).toBe('25');
    expect(screen.getAllByRole('button', { name: 'Cancel' })[0].className).toContain('min-h-11');
    await userEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]);
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith('example-podcast', 'episode-0'));
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(mockGetEpisodes).toHaveBeenCalledWith({ queueOffset: 25, queueLimit: 25 }));
  });

  it('commits queue numeric controls on blur after the draft is complete', async () => {
    mockGetEpisodes.mockResolvedValue([]);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Queue Control' }));
    const input = screen.getByLabelText('Play / Reprocess');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '30' } });
    expect(mockUpdateSettings).not.toHaveBeenCalled();
    fireEvent.blur(input);
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith({ queueManualBoost: 30 }));
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
});
