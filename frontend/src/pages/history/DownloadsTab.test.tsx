/** Render, filter and sort tests for the History Downloads tab. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import HistoryPage from '../HistoryPage';
import { clientName } from './DownloadsTab';
import type { DownloadHistoryEntry } from '../../api/types';

const mockGetDownloadHistory = vi.fn();
const mockGetSettings = vi.fn();

vi.mock('../../api/history', () => ({
  getProcessingHistory: vi.fn().mockResolvedValue({ history: [], total: 0, page: 1, limit: 20, totalPages: 1 }),
  getProcessingHistoryStats: vi.fn().mockResolvedValue(null),
  exportProcessingHistory: vi.fn(),
  downloadBlob: vi.fn(),
  getDownloadHistory: (...a: unknown[]) => mockGetDownloadHistory(...a),
}));

vi.mock('../../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
}));

vi.mock('../../api/feeds', () => ({
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: () => Promise.resolve({
      feeds: [{ slug: 'example-podcast', title: 'Example Podcast' }], lastRefreshCompletedAt: null,
    }),
  },
  ALL_SUBSCRIBER_KEYS_QUERY_KEY: ['subscriber-keys-all'],
  getAllSubscriberKeys: () => Promise.resolve([{
    id: 'abcdef0123456789', label: 'Phone', createdAt: '2026-10-01T00:00:00Z',
    lastUsedAt: null, revokedAt: null, feedSlug: 'example-podcast', feedTitle: 'Example Podcast',
  }]),
}));

function makeRow(overrides: Partial<DownloadHistoryEntry> = {}): DownloadHistoryEntry {
  return {
    id: 1,
    episodeId: 'a1b2c3d4e5f6',
    episodeTitle: 'Episode One',
    podcastSlug: 'example-podcast',
    podcastTitle: 'Example Podcast',
    clientIp: '203.0.113.5',
    userAgent: 'Overcast/3.0 (+http://overcast.fm/; iOS)',
    subscriberLabel: 'Phone',
    country: 'Exampleland',
    region: 'Region',
    city: 'Town',
    geoStatus: 'ok',
    firstSeenAt: '2026-10-14T10:00:00Z',
    lastSeenAt: '2026-10-14T10:05:00Z',
    requestCount: 2,
    ...overrides,
  };
}

function renderTab(rows: DownloadHistoryEntry[], geoEnabled = true) {
  mockGetDownloadHistory.mockResolvedValue({ downloads: rows, total: rows.length, page: 1, limit: 20 });
  mockGetSettings.mockResolvedValue({ downloadGeoLookupEnabled: { value: geoEnabled, isDefault: true } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/history?tab=downloads']}>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastParams = () => mockGetDownloadHistory.mock.calls[mockGetDownloadHistory.mock.calls.length - 1]?.[0];

describe('DownloadsTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders rows with shortened client and location', async () => {
    renderTab([makeRow()]);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Episode One')).toBeTruthy();
    expect(within(table).getByText('203.0.113.5')).toBeTruthy();
    expect(within(table).getByText('Town, Region, Exampleland')).toBeTruthy();
    expect(within(table).getByText('Overcast').getAttribute('title')).toContain('overcast.fm');
    expect(within(table).getByText('Phone')).toBeTruthy();
  });

  it('shows Local network, Unknown and the lookup-off hint', async () => {
    renderTab([
      makeRow({ id: 1, geoStatus: 'private', city: null }),
      makeRow({ id: 2, geoStatus: 'failed', city: null }),
      makeRow({ id: 3, geoStatus: null, city: null }),
    ], false);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Local network')).toBeTruthy();
    expect(within(table).getByText('Unknown')).toBeTruthy();
    expect(within(table).getByText('lookup off')).toBeTruthy();
  });

  it('refetches with the podcast filter', async () => {
    renderTab([makeRow()]);
    await screen.findByRole('table');
    await userEvent.selectOptions(screen.getByLabelText('Podcast'), 'example-podcast');
    await waitFor(() => expect(lastParams()).toMatchObject({ podcastSlug: 'example-podcast', page: 1 }));
  });

  it('refetches with the subscriber filter', async () => {
    renderTab([makeRow()]);
    await screen.findByRole('table');
    await screen.findByRole('option', { name: /Phone/ });
    await userEvent.selectOptions(screen.getByLabelText('Subscriber'), 'abcdef0123456789');
    await waitFor(() => expect(lastParams()).toMatchObject({ subscriberKeyId: 'abcdef0123456789' }));
  });

  it('refetches with date range and search', async () => {
    renderTab([makeRow()]);
    await screen.findByRole('table');
    await userEvent.type(screen.getByLabelText('Since'), '2026-10-01');
    await userEvent.type(screen.getByLabelText('Search'), '203.0');
    await waitFor(() => expect(lastParams()).toMatchObject({ since: '2026-10-01', search: '203.0' }));
  });

  it('flips sort direction when the active header is clicked', async () => {
    renderTab([makeRow()]);
    const table = await screen.findByRole('table');
    expect(lastParams()).toMatchObject({ sortBy: 'last_seen_at', sortDir: 'desc' });
    await userEvent.click(within(table).getByRole('button', { name: /Time/ }));
    await waitFor(() => expect(lastParams()).toMatchObject({ sortBy: 'last_seen_at', sortDir: 'asc' }));
    await userEvent.click(within(table).getByRole('button', { name: /Client IP/ }));
    await waitFor(() => expect(lastParams()).toMatchObject({ sortBy: 'client_ip' }));
  });

  it('switches tabs through the tab strip', async () => {
    renderTab([makeRow()]);
    await screen.findByRole('table');
    expect(screen.getByRole('tab', { name: 'Downloads' }).getAttribute('aria-selected')).toBe('true');
    await userEvent.click(screen.getByRole('tab', { name: 'Processing' }));
    expect(await screen.findByRole('button', { name: 'Export CSV' })).toBeTruthy();
  });
});

describe('clientName', () => {
  it('takes the first product token', () => {
    expect(clientName('Pocket Casts')).toBe('Pocket Casts');
    expect(clientName('AntennaPod/3.1 (Android)')).toBe('AntennaPod');
    expect(clientName('Mozilla (X11)')).toBe('Mozilla');
    expect(clientName('')).toBe('Unknown');
  });
});
