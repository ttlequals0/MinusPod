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

vi.mock('../../api/feeds', () => ({
  regenerateAllFeeds: (...args: unknown[]) => mockRegenerateAllFeeds(...args),
  feedsQueryOptions: { queryKey: ['feeds'], queryFn: () => Promise.resolve({ feeds: [{ slug: 'example-feed', title: 'Example Feed' }] }) },
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
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    expect(mockDeleteSubscriberKeyRecord).toHaveBeenCalledWith('example-feed', 'key-1');
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
