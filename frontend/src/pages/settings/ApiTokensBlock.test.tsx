import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ApiTokensBlock from './ApiTokensBlock';

const mockList = vi.fn();
const mockCreate = vi.fn();
const mockRevoke = vi.fn();
vi.mock('../../api/apiTokens', () => ({
  API_TOKENS_QUERY_KEY: ['api-tokens'],
  listApiTokens: () => mockList(),
  createApiToken: (name: string) => mockCreate(name),
  revokeApiToken: (id: number) => mockRevoke(id),
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
});

describe('ApiTokensBlock', () => {
  it('lists tokens with the prefix and last-used state', async () => {
    renderBlock();
    const table = await screen.findByRole('table');
    expect(within(table).getByText('home-assistant')).toBeTruthy();
    expect(within(table).getByText('mp_AbCdEfGh...')).toBeTruthy();
    expect(within(table).getByText('Never')).toBeTruthy();
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
    await screen.findByRole('table');
    await user.type(screen.getByLabelText('Token name'), '  cron ');
    await user.click(screen.getByRole('button', { name: 'Create token' }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith('cron'));
    const dialog = await screen.findByRole('dialog');
    expect((within(dialog).getByLabelText('New API token') as HTMLInputElement).value).toBe(PLAINTEXT);
    expect(within(dialog).getByText(/will not be shown again/i)).toBeTruthy();
    expect(within(dialog).getByText(/including provider keys in an export/i)).toBeTruthy();
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
    await screen.findByRole('table');
    await user.click(screen.getAllByRole('button', { name: 'Revoke home-assistant' })[0]);
    expect(mockRevoke).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Revoke token' }));
    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('cancelling the confirm does not revoke', async () => {
    const user = userEvent.setup();
    renderBlock();
    await screen.findByRole('table');
    await user.click(screen.getAllByRole('button', { name: 'Revoke home-assistant' })[0]);
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(mockRevoke).not.toHaveBeenCalled();
  });
});
