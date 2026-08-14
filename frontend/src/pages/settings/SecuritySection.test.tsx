/**
 * Component tests for SecuritySection (issue #461).
 *
 * The no-password warning must be state-aware:
 *   1. No password, no master passphrase: original full warning.
 *   2. No password, passphrase set: same severity, text acknowledges that
 *      the passphrase encrypts stored keys but does not restrict access.
 *   3. Password set: no warning at all.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SecuritySection from './SecuritySection';
import type { Settings } from '../../api/types';

vi.mock('../../api/providers', () => ({
  rotateMasterPassphrase: vi.fn(),
}));

vi.mock('../../api/auth', () => ({
  setPassword: vi.fn(),
  removePassword: vi.fn(),
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
  vi.clearAllMocks();
  mockGetSettings.mockResolvedValue(makeSettings());
  mockUpdateSettings.mockResolvedValue({ message: 'ok' });
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
    expect(screen.queryByText('Current Password')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Logout' })).not.toBeNull();
  });
});

const ADD_BUTTON_NAME = '+ Add agent';

describe('SecuritySection blocked user agents', () => {
  it('renders existing patterns as chips', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      jitBlockedUserAgents: { value: ['PocketCasts', '^atc/'], isDefault: false },
    }));
    renderSection({ isPasswordSet: true, cryptoReady: true });

    expect(await screen.findByText('PocketCasts')).toBeDefined();
    expect(screen.getByText('^atc/')).toBeDefined();
  });

  it('adding one calls updateSettings with the appended array', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      jitBlockedUserAgents: { value: ['PocketCasts'], isDefault: false },
    }));
    renderSection({ isPasswordSet: true, cryptoReady: true });

    await screen.findByText('PocketCasts');
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

    await screen.findByText('PocketCasts');
    await userEvent.click(screen.getByRole('button', { name: 'Remove PocketCasts' }));

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
