import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ApiTokensBlock from './ApiTokensBlock';

const mockList = vi.fn();
const mockCreate = vi.fn();
const mockRevoke = vi.fn();
const mockRevokeAll = vi.fn();
vi.mock('../../api/apiTokens', () => ({
  API_TOKENS_QUERY_KEY: ['api-tokens'],
  listApiTokens: () => mockList(),
  createApiToken: (name: string) => mockCreate(name),
  revokeApiToken: (id: number) => mockRevoke(id),
  revokeAllApiTokens: () => mockRevokeAll(),
}));

const PLAINTEXT = 'mp_' + 'x'.repeat(43);
const TOKEN = { id: 7, name: 'home-assistant', prefix: 'AbCdEfGh', createdAt: '2026-01-02T03:04:05Z', lastUsedAt: null };

let queryClient: QueryClient;
function renderBlock(isPasswordSet = true) {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApiTokensBlock isPasswordSet={isPasswordSet} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockList.mockReset().mockResolvedValue([TOKEN]);
  mockCreate.mockReset().mockResolvedValue({ ...TOKEN, id: 8, name: 'cron', token: PLAINTEXT });
  mockRevoke.mockReset().mockResolvedValue({ revoked: true });
  mockRevokeAll.mockReset().mockResolvedValue({ revoked: 1 });
});

describe('ApiTokensBlock', () => {
  it('shows a short intro and compact rows with the status line', async () => {
    renderBlock();
    expect(await screen.findByText('home-assistant')).toBeTruthy();
    expect(screen.getByText('Tokens let scripts call the API.')).toBeTruthy();
    expect(screen.queryByText(/Authorization/)).toBeNull();
    expect(screen.getByText('Never used')).toBeTruthy();
    expect(screen.getByText('1 token')).toBeTruthy();
    expect(screen.queryByText('mp_AbCdEfGh...')).toBeNull();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('shows Last used with a date once a token has been used', async () => {
    mockList.mockResolvedValue([{ ...TOKEN, lastUsedAt: '2026-11-27T12:00:00Z' }]);
    renderBlock();
    expect(await screen.findByText(/^Last used /)).toBeTruthy();
    expect(screen.queryByText('Never used')).toBeNull();
  });

  it('views details from the row actions menu', async () => {
    const user = userEvent.setup();
    renderBlock();
    await user.click(await screen.findByRole('button', { name: 'Actions for home-assistant' }));
    await user.click(screen.getByRole('menuitem', { name: 'View details' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('mp_AbCdEfGh...')).toBeTruthy();
    expect(within(dialog).getByText('Never')).toBeTruthy();
    expect(within(dialog).getByText('Created')).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('hides Revoke all with fewer than two tokens and shows it with two', async () => {
    renderBlock();
    await screen.findByText('home-assistant');
    expect(screen.queryByRole('button', { name: 'Revoke all' })).toBeNull();
    mockList.mockResolvedValue([TOKEN, { ...TOKEN, id: 9, name: 'cron' }]);
    await act(() => queryClient.invalidateQueries({ queryKey: ['api-tokens'] }));
    expect(await screen.findByRole('button', { name: 'Revoke all' })).toBeTruthy();
    expect(screen.getByText('2 tokens')).toBeTruthy();
  });

  it('shows an empty state', async () => {
    mockList.mockResolvedValue([]);
    renderBlock();
    expect(await screen.findByText('No API tokens yet.')).toBeTruthy();
  });

  it('explains and skips the request when no password is set', () => {
    renderBlock(false);
    expect(screen.getByText(/need an application password/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Create token' })).toBeNull();
    expect(mockList).not.toHaveBeenCalled();
  });

  it('creates a token, reveals it once and keeps it out of the query cache', async () => {
    const user = userEvent.setup();
    renderBlock();
    await screen.findByText('home-assistant');
    await user.type(screen.getByLabelText('Token name'), '  cron ');
    await user.click(screen.getByRole('button', { name: 'Create token' }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith('cron'));
    const dialog = await screen.findByRole('dialog');
    expect((within(dialog).getByLabelText('New API token') as HTMLTextAreaElement).value).toBe(PLAINTEXT);
    expect(within(dialog).getByText(/will not be shown again/i)).toBeTruthy();
    expect(within(dialog).getByText(/including provider keys in an export/i)).toBeTruthy();
    expect(within(dialog).getByText(/Authorization: Bearer <token>/)).toBeTruthy();
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(PLAINTEXT);
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);

    await user.click(within(dialog).getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByDisplayValue(PLAINTEXT)).toBeNull();
  });

  it('shows the server error when creation fails', async () => {
    mockCreate.mockRejectedValue(new Error('An active API token already has that name'));
    const user = userEvent.setup();
    renderBlock();
    await user.type(await screen.findByLabelText('Token name'), 'dup');
    await user.click(screen.getByRole('button', { name: 'Create token' }));
    expect((await screen.findByRole('alert')).textContent).toContain('already has that name');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('revokes only after confirming', async () => {
    const user = userEvent.setup();
    renderBlock();
    await screen.findByText('home-assistant');
    await user.click(screen.getByRole('button', { name: 'Actions for home-assistant' }));
    await user.click(screen.getByRole('menuitem', { name: 'Revoke' }));
    expect(mockRevoke).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Revoke token' }));
    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText('Token name')));
  });

  it('revokes every token only after confirming', async () => {
    mockList.mockResolvedValue([TOKEN, { ...TOKEN, id: 9, name: 'cron' }]);
    const user = userEvent.setup();
    renderBlock();
    await screen.findByText('home-assistant');
    await user.click(screen.getByRole('button', { name: 'Revoke all' }));
    expect(mockRevokeAll).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Revoke all tokens' }));
    await waitFor(() => expect(mockRevokeAll).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('cancelling the confirm does not revoke', async () => {
    const user = userEvent.setup();
    renderBlock();
    await screen.findByText('home-assistant');
    await user.click(screen.getByRole('button', { name: 'Actions for home-assistant' }));
    await user.click(screen.getByRole('menuitem', { name: 'Revoke' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(mockRevoke).not.toHaveBeenCalled();
  });
});
