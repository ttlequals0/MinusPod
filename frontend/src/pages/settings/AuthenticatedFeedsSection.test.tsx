/** Component tests for the key toggle and regenerate controls in AuthenticatedFeedsSection.tsx. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AuthenticatedFeedsSection from './AuthenticatedFeedsSection';
import type { Settings } from '../../api/types';

const mockGetSettings = vi.fn();
const mockUpdateSettings = vi.fn();
const mockRegenerateFeedKey = vi.fn();

vi.mock('../../api/settings', () => ({
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
  updateSettings: (...args: unknown[]) => mockUpdateSettings(...args),
  regenerateFeedKey: (...args: unknown[]) => mockRegenerateFeedKey(...args),
}));

const mockRegenerateAllFeeds = vi.fn();
const mockGetSubscriberKeys = vi.fn();
const mockCreateSubscriberKey = vi.fn();
const mockRevokeSubscriberKey = vi.fn();
const mockDeleteSubscriberKeyRecord = vi.fn();
const mockGetAllSubscriberKeys = vi.fn();
const mockFeeds = [
  { slug: 'zeta', title: 'Zeta Show' },
  { slug: 'bravo', title: 'Source Title', titleOverride: 'bravo show' },
  { slug: 'alpha', title: 'Alpha Show' },
];

vi.mock('../../api/feeds', () => ({
  regenerateAllFeeds: (...args: unknown[]) => mockRegenerateAllFeeds(...args),
  feedsQueryOptions: { queryKey: ['feeds'], queryFn: () => Promise.resolve({ feeds: mockFeeds }) },
  getAllSubscriberKeys: (...args: unknown[]) => mockGetAllSubscriberKeys(...args),
  ALL_SUBSCRIBER_KEYS_QUERY_KEY: ['subscriber-keys-all'],
  getSubscriberKeys: (...args: unknown[]) => mockGetSubscriberKeys(...args),
  createSubscriberKey: (...args: unknown[]) => mockCreateSubscriberKey(...args),
  revokeSubscriberKey: (...args: unknown[]) => mockRevokeSubscriberKey(...args),
  deleteSubscriberKeyRecord: (...args: unknown[]) => mockDeleteSubscriberKeyRecord(...args),
}));

function makeSettings(overrides: Partial<Settings> = {}): Settings {
  return {
    feedAuthEnabled: { value: false, isDefault: true },
    feedAuthKey: null,
    jitBlockedUserAgents: { value: [], isDefault: true },
    ...overrides,
  } as Settings;
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
}

function renderSection() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <AuthenticatedFeedsSection />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUpdateSettings.mockResolvedValue({ message: 'ok' });
  mockRegenerateFeedKey.mockResolvedValue({ feedAuthKey: 'new-key' });
  mockRegenerateAllFeeds.mockResolvedValue({ message: 'ok', feedCount: 3 });
  mockGetSubscriberKeys.mockResolvedValue([]);
  mockGetAllSubscriberKeys.mockResolvedValue([]);
});

describe('AuthenticatedFeedsSection subscriber keys', () => {
  it('copies a newly created subscriber URL using the shared copy control', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockCreateSubscriberKey.mockResolvedValue({
      id: 'key-1', label: 'Living room', created_at: '2026-09-10T00:00:00Z',
      last_used_at: null, revoked_at: null, token: 'token', feedUrl: 'https://example.com/feed.xml?key=token',
    });
    renderSection();
    await userEvent.type(await screen.findByLabelText('Subscriber label'), 'Living room');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByRole('button', { name: 'Copy subscriber feed URL' })).toBeDefined();
  });

  it('only offers deletion for a revoked key and confirms before deleting it', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockGetSubscriberKeys.mockResolvedValue([{ id: 'key-1', label: 'Old device', created_at: '2026-09-10T00:00:00Z', last_used_at: null, revoked_at: '2026-09-10T01:00:00Z' }]);
    mockDeleteSubscriberKeyRecord.mockResolvedValue({ deleted: true });
    renderSection();
    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete key record' }));
    expect(mockDeleteSubscriberKeyRecord).toHaveBeenCalledWith('alpha', 'key-1');
  });
});

describe('AuthenticatedFeedsSection all subscriber feeds', () => {
  it('lists every key with its feed title and label', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockGetAllSubscriberKeys.mockResolvedValue([
      { id: 'k1', label: 'Living room', createdAt: '2026-09-10T00:00:00Z', lastUsedAt: null, revokedAt: null, feedSlug: 'alpha', feedTitle: 'Alpha Show' },
      { id: 'k2', label: 'Old device', createdAt: '2026-09-10T00:00:00Z', lastUsedAt: null, revokedAt: '2026-09-11T00:00:00Z', feedSlug: 'zeta', feedTitle: 'Zeta Show' },
    ]);
    renderSection();
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Alpha Show')).toBeDefined();
    expect(within(table).getByText('Living room')).toBeDefined();
    expect(within(table).getByText('Active')).toBeDefined();
    expect(within(table).getByText('Revoked')).toBeDefined();
  });

  it('shows an error instead of the empty state when the list fails to load', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockGetAllSubscriberKeys.mockRejectedValue(new Error('down'));
    renderSection();
    expect(await screen.findByText('Could not load subscriber feeds.')).toBeDefined();
  });

  it('revokes a key using its own feed slug', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockGetAllSubscriberKeys.mockResolvedValue([
      { id: 'k1', label: 'Living room', createdAt: '2026-09-10T00:00:00Z', lastUsedAt: null, revokedAt: null, feedSlug: 'zeta', feedTitle: 'Zeta Show' },
    ]);
    mockRevokeSubscriberKey.mockResolvedValue({ revoked: true });
    renderSection();
    const table = await screen.findByRole('table');
    await userEvent.click(within(table).getByRole('button', { name: 'Revoke' }));
    expect(mockRevokeSubscriberKey).toHaveBeenCalledWith('zeta', 'k1');
  });
});

describe('AuthenticatedFeedsSection feed picker', () => {
  it('sorts feeds alphabetically by display title', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSection();
    const select = await screen.findByLabelText('Feed');
    const options = within(select).getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(['Alpha Show', 'bravo show', 'Zeta Show']);
  });
});

describe('AuthenticatedFeedsSection key toggle', () => {
  it('toggling the switch calls updateSettings with feedAuthEnabled', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSection();

    const toggle = await screen.findByLabelText('Require key in feed URLs');
    await userEvent.click(toggle);

    expect(mockUpdateSettings).toHaveBeenCalledWith({ feedAuthEnabled: true });
  });
});

describe('AuthenticatedFeedsSection regenerate controls', () => {
  it('shows regenerate controls once auth is enabled, and confirming regenerate key calls the API', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      feedAuthEnabled: { value: true, isDefault: false },
      feedAuthKey: 'existing-key',
    }));
    renderSection();

    await userEvent.click(await screen.findByRole('button', { name: 'Regenerate key' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Regenerate key' }));

    expect(mockRegenerateFeedKey).toHaveBeenCalled();
  });

  it('regenerate feeds button calls regenerateAllFeeds', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      feedAuthEnabled: { value: true, isDefault: false },
      feedAuthKey: 'existing-key',
    }));
    renderSection();

    await userEvent.click(await screen.findByRole('button', { name: 'Regenerate feeds' }));

    expect(mockRegenerateAllFeeds).toHaveBeenCalled();
    expect(await screen.findByText('Regenerated 3 feeds')).toBeDefined();
  });
});

describe('AuthenticatedFeedsSection download history', () => {
  it('saves the geo lookup toggle and a valid retention value', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      downloadGeoLookupEnabled: { value: false, isDefault: true },
      downloadHistoryRetentionDays: { value: 90, isDefault: true },
    }));
    localStorage.setItem('settings-section-authenticated-feeds', 'true');
    renderSection();
    expect(await screen.findByText(/Sends public client IPs to ipwho.is over HTTPS; off by default/)).toBeDefined();
    await userEvent.click(screen.getByRole('switch', { name: 'Look up client locations' }));
    expect(mockUpdateSettings).toHaveBeenCalledWith({ downloadGeoLookupEnabled: true });
    const days = screen.getByLabelText('Keep history for (days)');
    await userEvent.clear(days);
    await userEvent.type(days, '30');
    await userEvent.tab();
    expect(mockUpdateSettings).toHaveBeenCalledWith({ downloadHistoryRetentionDays: 30 });
  });

  it('rejects an out-of-range retention value without saving', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    localStorage.setItem('settings-section-authenticated-feeds', 'true');
    renderSection();
    const days = await screen.findByLabelText('Keep history for (days)');
    await userEvent.clear(days);
    await userEvent.type(days, '0');
    expect(screen.getByText(/Enter a whole number from 1 to 3650/)).toBeDefined();
    await userEvent.tab();
    expect(mockUpdateSettings).not.toHaveBeenCalled();
  });
});
