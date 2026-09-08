/**
 * Tests for the Queue Control section: the moved Global Defaults queue
 * pieces (process-new-first toggle, priority boosts) and the offline queue /
 * rate-limit hold blocks with their failed-GET guards.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
      enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
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
      enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
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
      enabled: true, holdUntil: '2026-08-30T20:00:00Z', llmUsageUrl: '', rateLimitProbeMinutes: 5,
    });
    renderSection();
    await waitFor(() => {
      expect(screen.getByText(/Queue paused until/)).toBeTruthy();
    });
    // No give-up window: held episodes stay in the normal queue.
    expect(screen.queryByLabelText('Give up after:', { selector: '#rate-limit-hold-ttl' })).toBeNull();
  });

  it('renders the offline queue failed-GET guard instead of the editable form', async () => {
    mocked.getOfflineQueueSettings.mockRejectedValue(new Error('boom'));
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
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
      enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
    });
    renderSection({}, new Set(['settings-section-queue-control']));
    await waitFor(() => {
      expect(screen.getByLabelText('Offline queue toggle')).toBeTruthy();
    });
    expect(mocked.getOfflineQueueSettings).toHaveBeenCalled();
  });

  it('renders the usage URL and probe interval with their current values', async () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, holdUntil: null,
      llmUsageUrl: 'https://your-proxy:8001/v1/usage', rateLimitProbeMinutes: 10,
    });
    renderSection();
    await waitFor(() => {
      expect((screen.getByLabelText('Usage endpoint (optional)') as HTMLInputElement).value)
        .toBe('https://your-proxy:8001/v1/usage');
    });
    expect((screen.getByLabelText('Check every:') as HTMLInputElement).value).toBe('10');
  });

  it('saves an edited usage URL and probe interval together', async () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
    });
    mocked.updateRateLimitHoldSettings.mockResolvedValue({
      enabled: false, holdUntil: null,
      llmUsageUrl: 'https://your-proxy:8001/v1/usage', rateLimitProbeMinutes: 15,
    });
    const user = userEvent.setup();
    renderSection();
    const urlInput = await screen.findByLabelText('Usage endpoint (optional)');
    await user.type(urlInput, 'https://your-proxy:8001/v1/usage');
    const minutesInput = screen.getByLabelText('Check every:');
    await user.clear(minutesInput);
    await user.type(minutesInput, '15');
    const block = urlInput.closest('.space-y-4') as HTMLElement;
    await user.click(within(block).getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(mocked.updateRateLimitHoldSettings).toHaveBeenCalledWith({
        enabled: false,
        llmUsageUrl: 'https://your-proxy:8001/v1/usage',
        rateLimitProbeMinutes: 15,
      });
    });
  });

  it('does not fetch while collapsed and unmatched', () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
    });
    renderSection({}, new Set(['settings-section-something-else']));
    expect(mocked.getOfflineQueueSettings).not.toHaveBeenCalled();
    expect(mocked.getRateLimitHoldSettings).not.toHaveBeenCalled();
  });
});

describe('QueueControlSection loading placeholder', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows row skeletons instead of a Loading line while the queries are pending', async () => {
    mocked.getOfflineQueueSettings.mockReturnValue(new Promise(() => {}));
    mocked.getRateLimitHoldSettings.mockReturnValue(new Promise(() => {}));
    renderSection();
    await waitFor(() => expect(screen.getAllByTestId('skeleton-rows').length).toBe(2));
    expect(screen.queryByText('Loading...')).toBeNull();
  });

  it('drops the skeletons once the settings land', async () => {
    mocked.getOfflineQueueSettings.mockResolvedValue({
      enabled: false, ttlHours: 48, deferredCount: 0,
    });
    mocked.getRateLimitHoldSettings.mockResolvedValue({
      enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
    });
    renderSection();
    await waitFor(() => expect(screen.queryAllByTestId('skeleton-rows')).toHaveLength(0));
  });
});
