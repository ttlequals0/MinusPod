/** Render, filter and sort tests for the History Downloads tab. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import HistoryPage from '../HistoryPage';
import type { DownloadHistoryEntry } from '../../api/types';

const mockGetDownloadHistory = vi.fn();
const mockGetSettings = vi.fn();
const mockUpdateSettings = vi.fn();
const mockRevoke = vi.fn();

vi.mock('../../api/history', () => ({
  getProcessingHistory: vi.fn().mockResolvedValue({ history: [], total: 0, page: 1, limit: 20, totalPages: 1 }),
  getProcessingHistoryStats: vi.fn().mockResolvedValue(null),
  exportProcessingHistory: vi.fn(),
  downloadBlob: vi.fn(),
  getDownloadHistory: (...a: unknown[]) => mockGetDownloadHistory(...a),
}));

vi.mock('../../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
  updateSettings: (...a: unknown[]) => mockUpdateSettings(...a),
}));

vi.mock('../../api/feeds', () => ({
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: () => Promise.resolve({
      feeds: [{ slug: 'example-podcast', title: 'Example Podcast' }], lastRefreshCompletedAt: null,
    }),
  },
  ALL_SUBSCRIBER_KEYS_QUERY_KEY: ['subscriber-keys-all'],
  revokeSubscriberKey: (...a: unknown[]) => mockRevoke(...a),
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
    subscriberKeyId: 'abcdef0123456789',
    subscriberRevoked: false,
    userAgentBlocked: false,
    lastRequestLine: 'GET /episodes/example-podcast/a1b2c3d4e5f6.mp3?key=[redacted]',
    lastRequestHeaders: { Range: 'bytes=0-' },
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

function renderTab(rows: DownloadHistoryEntry[], geoEnabled = true, blocked: string[] = []) {
  mockGetDownloadHistory.mockResolvedValue({ downloads: rows, total: rows.length, page: 1, limit: 20 });
  mockGetSettings.mockResolvedValue({
    downloadGeoLookupEnabled: { value: geoEnabled, isDefault: true },
    jitBlockedUserAgents: { value: blocked, isDefault: blocked.length === 0 },
  });
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
      makeRow({ id: 4, geoStatus: 'unresolvable', city: null }),
    ], false);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Local network')).toBeTruthy();
    expect(within(table).getAllByText('Unknown')).toHaveLength(2);
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

describe('DownloadsTab row actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateSettings.mockResolvedValue({});
    mockRevoke.mockResolvedValue({ revoked: true });
  });

  const openMenu = async () => {
    const table = await screen.findByRole('table');
    await userEvent.click(within(table).getByRole('button', { name: 'Actions' }));
  };

  it('lists the view items before Block and Revoke', async () => {
    renderTab([makeRow()]);
    await openMenu();
    expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual([
      'View user agent', 'View request', 'Block user agent', 'Revoke subscriber',
    ]);
  });

  it('shows the full raw user agent with a copy button', async () => {
    renderTab([makeRow({ userAgent: 'Overcast/3.0 (+http://overcast.fm/; iOS)' })]);
    await openMenu();
    await userEvent.click(screen.getByRole('menuitem', { name: 'View user agent' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Overcast/3.0 (+http://overcast.fm/; iOS)')).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Copy' })).toBeTruthy();
  });

  it('shows the last request line and headers', async () => {
    renderTab([makeRow()]);
    await openMenu();
    await userEvent.click(screen.getByRole('menuitem', { name: 'View request' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/GET \/episodes\/example-podcast\/a1b2c3d4e5f6\.mp3\?key=\[redacted\]/)).toBeTruthy();
    expect(within(dialog).getByText(/Range: bytes=0-/)).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: 'Copy' })).toBeTruthy();
  });

  it('disables View request when nothing was captured', async () => {
    renderTab([makeRow({ lastRequestLine: null, lastRequestHeaders: null })]);
    await openMenu();
    const item = screen.getByRole('menuitem', { name: 'View request' }) as HTMLButtonElement;
    expect(item.disabled).toBe(true);
    expect(item.title).toBe('No request captured');
  });

  it('puts the mobile Actions trigger in the card header and lists requests as a detail', async () => {
    renderTab([makeRow()]);
    await screen.findByRole('table');
    const card = screen.getAllByRole('link', { name: 'Episode One' })[0].parentElement as HTMLElement;
    expect(within(card).getByRole('button', { name: 'Actions' })).toBeTruthy();
    expect(screen.getByText('Requests', { selector: 'dt' })).toBeTruthy();
  });

  it('offers Block and Revoke for a subscriber row', async () => {
    renderTab([makeRow()]);
    await openMenu();
    expect(screen.getByRole('menuitem', { name: 'Block user agent' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Revoke subscriber' })).toBeTruthy();
  });

  it('offers only Block for a global-key row', async () => {
    renderTab([makeRow({ subscriberLabel: null, subscriberKeyId: null })]);
    await openMenu();
    expect(screen.getByRole('menuitem', { name: 'Block user agent' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Revoke subscriber' })).toBeNull();
  });

  it('confirms the exact pattern then appends it without duplicating', async () => {
    renderTab([makeRow({ userAgent: 'Pocket Casts/7.0 (Android)' })], true, ['AntennaPod']);
    await openMenu();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Block user agent' }));
    expect(await screen.findByText('^Pocket Casts')).toBeTruthy();
    const calls = mockGetDownloadHistory.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: 'Block user agent' }));
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith({
      jitBlockedUserAgents: ['AntennaPod', '^Pocket Casts'],
    }));
    await waitFor(() => expect(mockGetDownloadHistory.mock.calls.length).toBeGreaterThan(calls));
  });

  it('builds the list from freshly fetched settings, not the cached copy', async () => {
    renderTab([makeRow({ userAgent: 'Pocket Casts' })], true, ['AntennaPod']);
    await openMenu();
    mockGetSettings.mockResolvedValue({
      downloadGeoLookupEnabled: { value: true, isDefault: true },
      jitBlockedUserAgents: { value: ['AntennaPod', 'Added elsewhere'], isDefault: false },
    });
    await userEvent.click(screen.getByRole('menuitem', { name: 'Block user agent' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Block user agent' }));
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith({
      jitBlockedUserAgents: ['AntennaPod', 'Added elsewhere', '^Pocket Casts'],
    }));
  });

  it('does not duplicate a pattern that already exists in the fresh list', async () => {
    renderTab([makeRow({ userAgent: 'Pocket Casts' })], true, []);
    await openMenu();
    mockGetSettings.mockResolvedValue({
      downloadGeoLookupEnabled: { value: true, isDefault: true },
      jitBlockedUserAgents: { value: ['^Pocket Casts'], isDefault: false },
    });
    await userEvent.click(screen.getByRole('menuitem', { name: 'Block user agent' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Block user agent' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Block' })).toBeNull());
    expect(mockUpdateSettings).not.toHaveBeenCalled();
  });

  it('disables Block and shows a badge when the agent already matches', async () => {
    renderTab([makeRow({ userAgent: 'Pocket Casts', userAgentBlocked: true })], true, ['^pocket']);
    const table = await screen.findByRole('table');
    expect(await within(table).findByText('Blocked')).toBeTruthy();
    await userEvent.click(within(table).getByRole('button', { name: 'Actions' }));
    const item = screen.getByRole('menuitem', { name: 'Block user agent' }) as HTMLButtonElement;
    expect(item.disabled).toBe(true);
    expect(item.title).toBe('Already blocked');
  });

  it('disables Block when the user agent has no product token', async () => {
    renderTab([makeRow({ userAgent: '/1.0 (x)' })]);
    await openMenu();
    const item = screen.getByRole('menuitem', { name: 'Block user agent' }) as HTMLButtonElement;
    expect(item.disabled).toBe(true);
    expect(item.title).toBe('No user agent');
  });

  it('revokes with slug and id then refetches', async () => {
    renderTab([makeRow()]);
    await openMenu();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Revoke subscriber' }));
    expect(await screen.findByText(/Phone/, { selector: 'strong' })).toBeTruthy();
    const calls = mockGetDownloadHistory.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: 'Revoke subscriber' }));
    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith('example-podcast', 'abcdef0123456789'));
    await waitFor(() => expect(mockGetDownloadHistory.mock.calls.length).toBeGreaterThan(calls));
  });

  it('shows Revoked and disables Revoke for a revoked key', async () => {
    renderTab([makeRow({ subscriberRevoked: true })]);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Revoked')).toBeTruthy();
    await userEvent.click(within(table).getByRole('button', { name: 'Actions' }));
    const item = screen.getByRole('menuitem', { name: 'Revoke subscriber' }) as HTMLButtonElement;
    expect(item.disabled).toBe(true);
    expect(item.title).toBe('Already revoked');
  });

  it('shows the error when the update fails', async () => {
    mockUpdateSettings.mockRejectedValueOnce(new Error('entries must be 1-200 characters'));
    renderTab([makeRow({ userAgent: 'Pocket Casts' })]);
    await openMenu();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Block user agent' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Block user agent' }));
    expect(await screen.findByText('entries must be 1-200 characters')).toBeTruthy();
  });
});
