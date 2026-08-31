/**
 * Tests for the Queue Control section: the moved Global Defaults queue
 * pieces (process-new-first toggle, priority boosts) and the offline queue /
 * rate-limit hold blocks with their failed-GET guards.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import QueueControlSection from './QueueControlSection';
import { SettingsSearchContext } from '../../context/SettingsSearchContext';
import * as settingsApi from '../../api/settings';

vi.mock('../../api/settings', () => ({
  getOfflineQueueSettings: vi.fn(),
  updateOfflineQueueSettings: vi.fn(),
  getRateLimitHoldSettings: vi.fn(),
  updateRateLimitHoldSettings: vi.fn(),
}));

const mocked = vi.mocked(settingsApi);

function renderSection(
  overrides: Partial<Parameters<typeof QueueControlSection>[0]> = {},
  searchMatches: Set<string> | null = null,
) {
  // Both hold-block fetches are gated on the section being on screen, so seed
  // the persisted open flag the way a user who expanded it before would,
  // unless the test is exercising the search-reveal path instead.
  localStorage.setItem('settings-section-queue-control',
                       searchMatches ? 'false' : 'true');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = {
    processNewEpisodesFirst: true,
    onProcessNewEpisodesFirstChange: vi.fn(),
    queueManualBoost: 20,
    onQueueManualBoostChange: vi.fn(),
    queueFreshBoost: 5,
    onQueueFreshBoostChange: vi.fn(),
    queueBulkBoost: 0,
    onQueueBulkBoostChange: vi.fn(),
    ...overrides,
  };
  const utils = render(
    <QueryClientProvider client={client}>
      <SettingsSearchContext.Provider value={searchMatches}>
        <QueueControlSection {...props} />
      </SettingsSearchContext.Provider>
    </QueryClientProvider>
  );
  return { ...utils, props };
}

describe('QueueControlSection', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the process-new-first toggle with its current state', () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, holdUntil: null, holdCount: 0,
    });
    renderSection();
    const toggle = screen.getByRole('switch', { name: 'Process new episodes first' });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
  });

  it('renders the three boost fields with their current values', () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, holdUntil: null, holdCount: 0,
    });
    renderSection();
    expect((screen.getByLabelText('Play / Reprocess') as HTMLInputElement).value).toBe('20');
    expect((screen.getByLabelText('New episode') as HTMLInputElement).value).toBe('5');
    expect((screen.getByLabelText('Reprocess All') as HTMLInputElement).value).toBe('0');
  });

  it('shows an active rate-limit hold banner', async () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: true, ttlHours: 48,
      holdUntil: '2026-08-30T20:00:00Z', holdCount: 2,
    });
    renderSection();
    await waitFor(() => {
      expect(screen.getByText(/Queue paused until/)).toBeTruthy();
    });
    expect(screen.getByText(/2 episodes waiting/)).toBeTruthy();
  });

  it('renders the offline queue failed-GET guard instead of the editable form', async () => {
    mocked.getOfflineQueueSettings.mockRejectedValue(new Error('boom'));
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, holdUntil: null, holdCount: 0,
    });
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Could not load offline queue settings.')).toBeTruthy();
    });
    // The offline form's own TTL field must not render from fallback defaults.
    expect(screen.queryByLabelText('Give up after:', { selector: '#offline-queue-ttl' })).toBeNull();
  });

  it('loads its settings when a search reveals the collapsed section', async () => {
    // A search expands a matched section without calling onToggle, so gating
    // on the persisted open flag alone would strand it on "Loading...".
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: true, ttlHours: 12, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, holdUntil: null, holdCount: 0,
    });
    renderSection({}, new Set(['settings-section-queue-control']));
    await waitFor(() => {
      expect(screen.getByLabelText('Offline queue toggle')).toBeTruthy();
    });
    expect(mocked.getOfflineQueueSettings).toHaveBeenCalled();
  });

  it('does not fetch while collapsed and unmatched', () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, holdUntil: null, holdCount: 0,
    });
    renderSection({}, new Set(['settings-section-something-else']));
    expect(mocked.getOfflineQueueSettings).not.toHaveBeenCalled();
    expect(mocked.getRateLimitHoldSettings).not.toHaveBeenCalled();
  });
});
