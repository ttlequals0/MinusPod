/** Component tests for SubscriberKeysPanel: list, create, revoke, delete record. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SubscriberKeysPanel from './SubscriberKeysPanel';

const mockGetSubscriberKeys = vi.fn();
const mockCreateSubscriberKey = vi.fn();
const mockRevokeSubscriberKey = vi.fn();
const mockDeleteSubscriberKeyRecord = vi.fn();

vi.mock('../api/feeds', () => ({
  ALL_SUBSCRIBER_KEYS_QUERY_KEY: ['subscriber-keys-all'],
  getSubscriberKeys: (...args: unknown[]) => mockGetSubscriberKeys(...args),
  createSubscriberKey: (...args: unknown[]) => mockCreateSubscriberKey(...args),
  revokeSubscriberKey: (...args: unknown[]) => mockRevokeSubscriberKey(...args),
  deleteSubscriberKeyRecord: (...args: unknown[]) => mockDeleteSubscriberKeyRecord(...args),
}));

const activeKey = { id: 'key-1', label: 'Living room', created_at: '2026-09-10T00:00:00Z', last_used_at: null, revoked_at: null };
const revokedKey = { id: 'key-2', label: 'Old device', created_at: '2026-09-10T00:00:00Z', last_used_at: null, revoked_at: '2026-09-11T00:00:00Z' };

function renderPanel(queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
})) {
  render(
    <QueryClientProvider client={queryClient}>
      <SubscriberKeysPanel slug="example-podcast" />
    </QueryClientProvider>,
  );
  return queryClient;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetSubscriberKeys.mockResolvedValue([activeKey, revokedKey]);
});

describe('SubscriberKeysPanel', () => {
  it('lists the keys for the slug it is given', async () => {
    renderPanel();
    expect(await screen.findByText('Living room')).toBeDefined();
    expect(screen.getByText('Old device')).toBeDefined();
    expect(mockGetSubscriberKeys).toHaveBeenCalledWith('example-podcast');
  });

  it('posts the trimmed label on create and shows the one-time URL', async () => {
    mockCreateSubscriberKey.mockResolvedValue({
      ...activeKey, token: 't', feedUrl: 'https://example.com/feed.xml?key=t',
    });
    renderPanel();
    await userEvent.type(await screen.findByLabelText('Subscriber label'), ' Kitchen ');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(mockCreateSubscriberKey).toHaveBeenCalledWith('example-podcast', 'Kitchen');
    expect(await screen.findByRole('button', { name: 'Copy subscriber feed URL' })).toBeDefined();
  });

  it('revokes an active key and refreshes both key lists', async () => {
    mockRevokeSubscriberKey.mockResolvedValue({ revoked: true });
    const client = renderPanel();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke' }));
    expect(mockRevokeSubscriberKey).toHaveBeenCalledWith('example-podcast', 'key-1');
    await vi.waitFor(() => {
      const keys = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
      expect(keys).toContain(JSON.stringify(['subscriber-keys', 'example-podcast']));
      expect(keys).toContain(JSON.stringify(['subscriber-keys-all']));
    });
  });

  it('clears a revoke error once a retry succeeds', async () => {
    mockRevokeSubscriberKey.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce({ revoked: true });
    renderPanel();
    const button = await screen.findByRole('button', { name: 'Revoke' });
    await userEvent.click(button);
    expect(await screen.findByText(/Failed to revoke|boom/)).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }));
    await vi.waitFor(() => expect(screen.queryByText(/Failed to revoke|boom/)).toBeNull());
  });

  it('confirms before deleting a revoked key record', async () => {
    mockDeleteSubscriberKeyRecord.mockResolvedValue({ deleted: true });
    renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete key record' }));
    expect(mockDeleteSubscriberKeyRecord).toHaveBeenCalledWith('example-podcast', 'key-2');
  });
});
