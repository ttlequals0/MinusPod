/**
 * SecuritySection (#461): the no-password warning is state-aware. No password and no passphrase: full
 * warning; passphrase set: same severity, noting it encrypts keys but does not restrict access; password
 * set: none.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SecuritySection from './SecuritySection';
import type { Settings } from '../../api/types';

const mockSetPassword = vi.fn();
const mockListApiTokens = vi.fn();

vi.mock('../../api/auth', () => ({
  setPassword: (...args: unknown[]) => mockSetPassword(...args),
  removePassword: vi.fn(),
}));

vi.mock('../../api/apiTokens', () => ({
  API_TOKENS_QUERY_KEY: ['api-tokens'],
  listApiTokens: (...args: unknown[]) => mockListApiTokens(...args),
  revokeAllApiTokens: vi.fn(),
  revokeApiToken: vi.fn(),
  createApiToken: vi.fn(),
}));

const mockGetSettings = vi.fn();
const mockUpdateSettings = vi.fn();

vi.mock('../../api/settings', () => ({
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
  updateSettings: (...args: unknown[]) => mockUpdateSettings(...args),
}));

const NO_PASSWORD_TEXT = /This instance has no password, so anyone with network access has full control/;
const PASSPHRASE_TEXT = /The master passphrase encrypts stored API keys but does not restrict access to this app; anyone with network access still has full control\. Set a password below to protect it\./;

function makeSettings(overrides: Partial<Settings> = {}): Settings {
  return {
    jitBlockedUserAgents: { value: [], isDefault: true },
    ...overrides,
  } as Settings;
}

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
}

function renderSection(props: { isPasswordSet: boolean; cryptoReady?: boolean }) {
  return render(
    <QueryClientProvider client={makeClient()}>
      <SecuritySection
        isPasswordSet={props.isPasswordSet}
        cryptoReady={props.cryptoReady}
        logout={vi.fn()}
        refreshStatus={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem('settings-section-security', 'true');
  vi.clearAllMocks();
  mockGetSettings.mockResolvedValue(makeSettings());
  mockUpdateSettings.mockResolvedValue({ message: 'ok' });
  mockListApiTokens.mockResolvedValue([]);
  mockSetPassword.mockResolvedValue({ message: 'ok', passwordSet: true, apiTokensRevoked: 0 });
});

describe('SecuritySection hardened controls', () => {
  it('shows offline rotation instructions instead of accepting a passphrase', async () => {
    renderSection({ isPasswordSet: true, cryptoReady: true });
    expect(await screen.findByText(/Stop every MinusPod worker/)).toBeDefined();
    expect(screen.queryByRole('button', { name: /Rotate Master Passphrase/ })).toBeNull();
  });

});

describe('SecuritySection warning: no password, no passphrase', () => {
  it('shows the original full warning', () => {
    renderSection({ isPasswordSet: false, cryptoReady: false });

    expect(screen.queryByText(NO_PASSWORD_TEXT)).not.toBeNull();
    expect(screen.queryByText(PASSPHRASE_TEXT)).toBeNull();
  });
});

describe('SecuritySection warning: no password, passphrase set', () => {
  it('acknowledges the passphrase without weakening severity', () => {
    renderSection({ isPasswordSet: false, cryptoReady: true });

    const warning = screen.queryByText(PASSPHRASE_TEXT);
    expect(warning).not.toBeNull();
    expect(screen.queryByText(NO_PASSWORD_TEXT)).toBeNull();
    // Same warning severity styling as the original container.
    const container = warning!.closest('div');
    expect(container?.className).toContain('bg-warning/10');
    expect(container?.className).toContain('border-warning/20');
  });
});

describe('SecuritySection warning: password set', () => {
  it('shows no warning', () => {
    renderSection({ isPasswordSet: true, cryptoReady: true });

    expect(screen.queryByText(NO_PASSWORD_TEXT)).toBeNull();
    expect(screen.queryByText(PASSPHRASE_TEXT)).toBeNull();
    // Password-set state still renders its normal UI.
    expect(screen.queryByText('Current password')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Logout' })).not.toBeNull();
  });
});

describe('SecuritySection password form', () => {
  it('carries a hidden username field for password managers', () => {
    const { container } = renderSection({ isPasswordSet: true, cryptoReady: true });

    const username = container.querySelector('input[autocomplete="username"]');
    expect(username).not.toBeNull();
    expect((username as HTMLInputElement).hidden).toBe(true);
    expect(username!.closest('form')).toBe(
      container.querySelector('#newPassword')!.closest('form'));
  });
});

describe('SecuritySection revoke API tokens option', () => {
  const token = { id: 1, name: 'ci', prefix: 'a1b2c3d4', createdAt: '2026-01-01T00:00:00Z', lastUsedAt: null };

  async function submitChange(user: ReturnType<typeof userEvent.setup>) {
    await user.type(document.getElementById('currentPassword')!, 'old-password-123');
    await user.type(document.getElementById('newPassword')!, 'new-password-1234');
    await user.type(document.getElementById('confirmPassword')!, 'new-password-1234');
    await user.click(screen.getByRole('button', { name: 'Change password' }));
  }

  it('is checked by default and sends revokeApiTokens true', async () => {
    mockListApiTokens.mockResolvedValue([token]);
    renderSection({ isPasswordSet: true });
    const box = await screen.findByRole('checkbox', { name: 'Also revoke API tokens' });
    expect((box as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText('Uncheck to keep scripts and integrations working after the change.')).toBeDefined();
    const user = userEvent.setup();
    await submitChange(user);
    expect(mockSetPassword).toHaveBeenCalledWith('new-password-1234', 'old-password-123', true);
  });

  it('sends false when unchecked', async () => {
    mockListApiTokens.mockResolvedValue([token]);
    renderSection({ isPasswordSet: true });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('checkbox', { name: 'Also revoke API tokens' }));
    await submitChange(user);
    expect(mockSetPassword).toHaveBeenCalledWith('new-password-1234', 'old-password-123', false);
  });

  it('is hidden when no password is set or the loaded list is empty', async () => {
    renderSection({ isPasswordSet: false });
    expect(screen.queryByRole('checkbox', { name: 'Also revoke API tokens' })).toBeNull();
    renderSection({ isPasswordSet: true });
    await waitFor(() => expect(mockListApiTokens).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: 'Also revoke API tokens' })).toBeNull());
  });

  it('stays visible while the token list is loading', async () => {
    mockListApiTokens.mockReturnValue(new Promise(() => {}));
    renderSection({ isPasswordSet: true });
    expect(await screen.findByRole('checkbox', { name: 'Also revoke API tokens' })).toBeDefined();
  });

  it('stays visible when the token list fails to load', async () => {
    mockListApiTokens.mockRejectedValue(new Error('boom'));
    renderSection({ isPasswordSet: true });
    expect(await screen.findByRole('checkbox', { name: 'Also revoke API tokens' })).toBeDefined();
    await waitFor(() => expect(mockListApiTokens).toHaveBeenCalled());
    expect(screen.getByRole('checkbox', { name: 'Also revoke API tokens' })).toBeDefined();
  });
});

const ADD_BUTTON_NAME = 'Add agent';

describe('SecuritySection blocked user agents', () => {
  it('renders existing patterns as table rows with a named remove button', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      jitBlockedUserAgents: { value: ['PocketCasts', '^atc/'], isDefault: false },
    }));
    renderSection({ isPasswordSet: true, cryptoReady: true });

    expect((await screen.findAllByText('PocketCasts')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('^atc/').length).toBeGreaterThan(0);
    expect(screen.getByRole('columnheader', { name: 'Agent' })).toBeDefined();
    expect(screen.getAllByRole('button', { name: 'Remove ^atc/' }).length).toBeGreaterThan(0);
  });

  it('shows an empty state when no agents are listed', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSection({ isPasswordSet: true, cryptoReady: true });

    expect(await screen.findByText('No agents yet.')).toBeDefined();
  });

  it('adding one calls updateSettings with the appended array', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      jitBlockedUserAgents: { value: ['PocketCasts'], isDefault: false },
    }));
    renderSection({ isPasswordSet: true, cryptoReady: true });

    await screen.findAllByText('PocketCasts');
    await userEvent.click(screen.getByRole('button', { name: ADD_BUTTON_NAME }));
    await userEvent.type(screen.getByLabelText('New blocked agent pattern'), '^atc/');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(mockUpdateSettings).toHaveBeenCalledWith({
      jitBlockedUserAgents: ['PocketCasts', '^atc/'],
    });
  });

  it('removing one calls updateSettings with the filtered array', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      jitBlockedUserAgents: { value: ['PocketCasts', '^atc/'], isDefault: false },
    }));
    renderSection({ isPasswordSet: true, cryptoReady: true });

    await screen.findAllByText('PocketCasts');
    await userEvent.click(screen.getAllByRole('button', { name: 'Remove PocketCasts' })[0]);

    expect(mockUpdateSettings).toHaveBeenCalledWith({
      jitBlockedUserAgents: ['^atc/'],
    });
  });

  it('a failed add keeps the editor open with its value and shows the error', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockUpdateSettings.mockRejectedValueOnce(new Error('jitBlockedUserAgents entries must be 1-200 characters'));
    renderSection({ isPasswordSet: true, cryptoReady: true });

    await screen.findByRole('button', { name: ADD_BUTTON_NAME });
    await userEvent.click(screen.getByRole('button', { name: ADD_BUTTON_NAME }));
    const input = screen.getByLabelText('New blocked agent pattern');
    await userEvent.type(input, '^atc/');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByText('jitBlockedUserAgents entries must be 1-200 characters')).toBeDefined();
    expect((screen.getByLabelText('New blocked agent pattern') as HTMLInputElement).value).toBe('^atc/');
  });

  it('shows helper text explaining the matching rule', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSection({ isPasswordSet: true, cryptoReady: true });

    expect(await screen.findByText(/case-insensitive, matches anywhere in the agent string/i)).toBeDefined();
  });
});
