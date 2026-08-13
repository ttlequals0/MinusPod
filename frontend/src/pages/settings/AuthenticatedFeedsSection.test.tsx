/**
 * Component tests for the JIT-blocked user agent list in
 * AuthenticatedFeedsSection.tsx.
 *
 * Covers:
 *   - Existing patterns render as chips.
 *   - Adding one calls updateSettings with the appended array.
 *   - Removing one calls updateSettings with the filtered array.
 *   - A server error surfaces and keeps the editor open with its value.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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

vi.mock('../../api/feeds', () => ({
  regenerateAllFeeds: (...args: unknown[]) => mockRegenerateAllFeeds(...args),
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

const ADD_BUTTON_NAME = '+ Add agent';

beforeEach(() => {
  vi.clearAllMocks();
  mockUpdateSettings.mockResolvedValue({ message: 'ok' });
});

describe('AuthenticatedFeedsSection blocked user agents', () => {
  it('renders existing patterns as chips', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      jitBlockedUserAgents: { value: ['PocketCasts', '^atc/'], isDefault: false },
    }));
    renderSection();

    expect(await screen.findByText('PocketCasts')).toBeDefined();
    expect(screen.getByText('^atc/')).toBeDefined();
  });

  it('adding one calls updateSettings with the appended array', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      jitBlockedUserAgents: { value: ['PocketCasts'], isDefault: false },
    }));
    renderSection();

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
    renderSection();

    await screen.findByText('PocketCasts');
    await userEvent.click(screen.getByRole('button', { name: 'Remove PocketCasts' }));

    expect(mockUpdateSettings).toHaveBeenCalledWith({
      jitBlockedUserAgents: ['^atc/'],
    });
  });

  it('a failed add keeps the editor open with its value and shows the error', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockUpdateSettings.mockRejectedValueOnce(new Error('jitBlockedUserAgents entries must be 1-200 characters'));
    renderSection();

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
    renderSection();

    expect(await screen.findByText(/case-insensitive, matches anywhere in the agent string/i)).toBeDefined();
  });
});
