/**
 * "Version" column on the Processing History table (2.78.4): the per-run app_version, "-" for older rows.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import HistoryPage from './HistoryPage';
import type { ProcessingHistoryEntry } from '../api/types';

const mockGetProcessingHistory = vi.fn();
const mockGetProcessingHistoryStats = vi.fn();

vi.mock('../api/history', () => ({
  getProcessingHistory: (...a: unknown[]) => mockGetProcessingHistory(...a),
  getProcessingHistoryStats: (...a: unknown[]) => mockGetProcessingHistoryStats(...a),
  exportProcessingHistory: vi.fn(),
  downloadBlob: vi.fn(),
}));

vi.mock('../api/feeds', () => ({
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: () => Promise.resolve({ feeds: [], lastRefreshCompletedAt: null }),
  },
}));

function makeEntry(overrides: Partial<ProcessingHistoryEntry> = {}): ProcessingHistoryEntry {
  return {
    id: 1,
    podcastId: 1,
    podcastSlug: 'test-feed',
    podcastTitle: 'Test Feed',
    episodeId: 'ep1',
    episodeTitle: 'Episode One',
    processedAt: '2026-07-24T00:00:00Z',
    processingDurationSeconds: 12.5,
    status: 'completed',
    adsDetected: 2,
    reprocessNumber: 1,
    ...overrides,
  };
}

function renderPage(entries: ProcessingHistoryEntry[], avgProcessingTimeSeconds = 12.5, entry = '/') {
  mockGetProcessingHistory.mockResolvedValue({
    history: entries, total: entries.length, page: 1, limit: 20, totalPages: 1,
  });
  mockGetProcessingHistoryStats.mockResolvedValue({
    totalProcessed: entries.length, completedCount: entries.length, failedCount: 0,
    avgProcessingTimeSeconds, totalAdsDetected: 2, reprocessCount: 0,
    uniqueEpisodes: entries.length, totalInputTokens: 0, totalOutputTokens: 0, totalLlmCost: 0,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('HistoryPage: Version column', () => {
  it('renders the version for a row that has one', async () => {
    renderPage([makeEntry({ appVersion: '2.78.4' })]);
    await waitFor(() => {
      expect(screen.getByText('2.78.4')).toBeDefined();
    });
  });

  it('renders podcast and episode links in foreground text', async () => {
    renderPage([makeEntry()]);
    const podcast = (await screen.findAllByRole('link', { name: 'Test Feed' }))[0];
    const episode = screen.getAllByRole('link', { name: 'Episode One' })[0];
    expect(podcast.className).toContain('text-foreground');
    expect(episode.className).toContain('text-foreground');
  });

  it('labels the run time and audio length columns', async () => {
    renderPage([makeEntry()]);
    expect(await screen.findByRole('columnheader', { name: /Run time/ })).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: 'Audio length' })).toBeTruthy();
  });

  it('renders a dash for a row with no version', async () => {
    renderPage([makeEntry({ appVersion: null })]);
    let row: HTMLElement | null | undefined;
    await waitFor(() => {
      row = screen.getAllByText('Episode One').map((el) => el.closest('tr')).find(Boolean);
      expect(row).toBeTruthy();
    });
    const cell = row?.querySelector('td:nth-child(9)');
    expect(cell?.textContent).toBe('-');
  });

  it('sorts by version when the Version header is clicked', async () => {
    renderPage([makeEntry({ appVersion: '2.78.4' })]);
    await waitFor(() => {
      expect(screen.getByText('2.78.4')).toBeDefined();
    });

    mockGetProcessingHistory.mockClear();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Version/ }));

    await waitFor(() => {
      expect(mockGetProcessingHistory).toHaveBeenCalledWith(
        expect.objectContaining({ sortBy: 'app_version', sortDir: 'desc' }),
      );
    });
  });
});

describe('HistoryPage: duration formatting', () => {
  it('rounds to whole seconds and drops a zero seconds field', async () => {
    renderPage([makeEntry()], 1_079.6);

    expect(await screen.findByText('Avg processing time: 18m')).toBeDefined();
    expect(screen.queryByText('Avg processing time: 17m 59s')).toBeNull();
  });
});

describe('HistoryPage: chapter regeneration rows', () => {
  it('labels chapter runs and does not report ads detected', async () => {
    renderPage([makeEntry({ mode: 'chapters', adsDetected: 0 })]);

    expect(await screen.findAllByText('Chapter regeneration')).toHaveLength(2);
    expect(screen.getByText('Ads: -')).toBeDefined();
    const row = screen.getAllByText('Episode One').map((el) => el.closest('tr')).find(Boolean);
    expect(row?.querySelector('td:nth-child(6)')?.textContent).toBe('-');
  });
});

describe('HistoryPage: failure reason', () => {
  it('shows the reason a run failed', async () => {
    renderPage([makeEntry({ status: 'failed', errorMessage: 'Whisper API returned 401' })]);
    const shown = await screen.findAllByText('Whisper API returned 401');
    expect(shown.length).toBeGreaterThan(0);
  });

  it('keeps the full text reachable when the row truncates it', async () => {
    const long = 'Transcription failed: ' + 'x'.repeat(200);
    renderPage([makeEntry({ status: 'failed', errorMessage: long })]);
    const shown = await screen.findAllByText(long);
    expect(shown[0].getAttribute('title')).toBe(long);
  });

  it('says nothing extra for a completed run', async () => {
    renderPage([makeEntry({ status: 'completed', errorMessage: undefined })]);
    await screen.findAllByText('Episode One');
    expect(screen.queryByText(/Whisper API/)).toBeNull();
  });

  it('falls back to the status badge when a failure carries no message', async () => {
    renderPage([makeEntry({ status: 'failed', errorMessage: undefined })]);
    const badges = await screen.findAllByText('Failed');
    const titles = badges.map((el) => el.closest('[title]')?.getAttribute('title'));
    expect(titles).toContain('Processing failed');
  });
});

describe('HistoryPage summary tiles', () => {
  it('makes Completed and Failed buttons that are direct tiles and toggle the filter', async () => {
    const user = userEvent.setup();
    renderPage([makeEntry()]);
    const completed = await screen.findByRole('button', { name: /Completed/ });
    expect(completed.classList.contains('row-span-3')).toBe(true);
    expect(completed.getAttribute('aria-pressed')).toBe('false');
    await user.click(completed);
    expect(screen.getByRole('button', { name: /Completed/ }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: /Failed/ }).getAttribute('aria-pressed')).toBe('false');
    await user.click(screen.getByRole('button', { name: /Completed/ }));
    expect(screen.getByRole('button', { name: /Completed/ }).getAttribute('aria-pressed')).toBe('false');
  });
});

describe('HistoryPage loading placeholder', () => {
  it('shows header and row skeletons while the history query is pending', () => {
    mockGetProcessingHistory.mockReturnValueOnce(new Promise(() => {}));
    renderPage([]);
    expect(screen.getByRole('heading', { name: 'History', level: 1 })).toBeTruthy();
    expect(screen.getByTestId('skeleton-rows')).toBeTruthy();
  });

  it('drops the skeletons once the history lands', async () => {
    renderPage([makeEntry()]);
    await screen.findAllByText('Episode One');
    expect(screen.queryByTestId('skeleton-rows')).toBeNull();
  });
});

describe('HistoryPage list state in the URL', () => {
  const lastParams = () => mockGetProcessingHistory.mock.lastCall?.[0];

  it('requests the page named by ?page=2', async () => {
    renderPage([makeEntry()], 12.5, '/?page=2');
    await screen.findAllByText('Episode One');
    expect(lastParams()).toMatchObject({ page: 2 });
  });

  it('restores filter and sort from the URL', async () => {
    renderPage([makeEntry()], 12.5, '/?status=failed&sort=llmCost&dir=asc');
    await screen.findAllByText('Episode One');
    expect(lastParams()).toMatchObject({ status: 'failed', sortBy: 'llm_cost', sortDir: 'asc' });
  });

  it('resets to page 1 when the status filter changes', async () => {
    renderPage([makeEntry()], 12.5, '/?page=2');
    await screen.findAllByText('Episode One');
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'failed');
    await waitFor(() => expect(lastParams()).toMatchObject({ page: 1, status: 'failed' }));
  });

  it('resets to page 1 when the sort changes', async () => {
    renderPage([makeEntry()], 12.5, '/?page=2');
    await screen.findAllByText('Episode One');
    await userEvent.click(screen.getByRole('button', { name: /Ads/ }));
    await waitFor(() => expect(lastParams()).toMatchObject({ page: 1, sortBy: 'ads_detected' }));
  });
});
