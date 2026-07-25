/**
 * Render tests for the "Version" column on the Processing History table
 * (2.78.4): the app_version stamped per processing run, shown as "-" for
 * rows recorded before the column existed.
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

function renderPage(entries: ProcessingHistoryEntry[]) {
  mockGetProcessingHistory.mockResolvedValue({
    history: entries, total: entries.length, page: 1, limit: 20, totalPages: 1,
  });
  mockGetProcessingHistoryStats.mockResolvedValue({
    totalProcessed: entries.length, completedCount: entries.length, failedCount: 0,
    avgProcessingTimeSeconds: 12.5, totalAdsDetected: 2, reprocessCount: 0,
    uniqueEpisodes: entries.length, totalInputTokens: 0, totalOutputTokens: 0, totalLlmCost: 0,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
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
    await user.click(screen.getByText('Version'));

    await waitFor(() => {
      expect(mockGetProcessingHistory).toHaveBeenCalledWith(
        expect.objectContaining({ sortBy: 'app_version', sortDir: 'desc' }),
      );
    });
  });
});
