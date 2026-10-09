/**
 * Integration tests for per-prompt reset wiring on the Settings page (#626):
 * the two-click confirm fires the single-prompt reset endpoint, not the bulk
 * one, and re-seeds the textarea from the refetched settings.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Settings, { systemStatusRefetchInterval } from './Settings';
import type { Settings as SettingsShape, SettingValue, SystemStatus } from '../api/types';
import { makeFailoverTarget } from '../test/failover';
import { updateSettings } from '../api/settings';

// The Reset button that belongs to one prompt textarea, found by its label
// rather than by position, so reordering settings sections cannot break it.
function resetButtonFor(label: string) {
  const field = screen.getByLabelText(label).closest('div') as HTMLElement;
  return within(field).getByRole('button', { name: 'Reset' });
}

vi.mock('react-router', () => ({
  useLocation: () => ({ hash: '', pathname: '/settings', search: '' }),
}));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    isPasswordSet: true,
    logout: vi.fn(),
    refreshStatus: vi.fn(),
  }),
}));

// vi.mock calls are hoisted above imports/setup, so each stub must be a
// static top-level call rather than built from a loop over a name list.
vi.mock('./settings/SystemStatusSection', () => ({ default: () => null }));
vi.mock('./settings/StorageRetentionSection', () => ({ default: () => null }));
vi.mock('./settings/DataManagementSection', () => ({ default: () => null }));
vi.mock('./settings/DatabaseStatsSection', () => ({ default: () => null }));
vi.mock('./settings/NotificationsSection', () => ({ default: () => null }));
vi.mock('./settings/AuthenticatedFeedsSection', () => ({ default: () => null }));
vi.mock('./settings/SecuritySection', () => ({ default: () => null }));
vi.mock('./settings/ProcessingQueueSection', () => ({ default: () => null }));
vi.mock('./settings/AppearanceSection', () => ({ default: () => null }));
vi.mock('./settings/PodcastIndexSection', () => ({ default: () => null }));
vi.mock('./settings/LLMProviderSection', () => ({ default: () => null }));
vi.mock('./settings/AIModelsSection', () => ({ default: () => <div data-testid="ai-models-section" /> }));
vi.mock('./settings/FailoverSection', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./settings/FailoverSection')>();
  return {
    default: (props: Parameters<typeof actual.default>[0]) => (
      <div data-testid="failover-section"><actual.default {...props} /></div>
    ),
  };
});
vi.mock('./settings/StageTunablesSection', () => ({ default: () => <div data-testid="stage-tunables-section" /> }));
vi.mock('./settings/TranscriptionSection', () => ({ default: () => null }));
vi.mock('./settings/AudioSection', () => ({ default: () => null }));
vi.mock('./settings/CoverArtSection', () => ({ default: () => null }));
vi.mock('./settings/AdDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/GlobalDefaultsSection', () => ({ default: () => null }));
vi.mock('./settings/SegmentActionsSection', () => ({ default: () => null }));
vi.mock('./settings/Podcasting20Section', () => ({ default: () => null }));
vi.mock('./settings/AudioCueDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/PositionalPriorSection', () => ({ default: () => null }));
vi.mock('./settings/PatternCleanupSection', () => ({ default: () => <div data-testid="pattern-cleanup-section" /> }));
vi.mock('./settings/CommunityPatternsSection', () => ({ default: () => null }));
vi.mock('./settings/DatabaseBackupSection', () => ({ default: () => null }));
vi.mock('./settings/QueueControlSection', () => ({ default: () => null }));
vi.mock('./settings/TranscriptNormalizationSection', () => ({ default: () => null }));

const mockGetSettings = vi.fn();
const mockGetModels = vi.fn().mockResolvedValue([]);
const mockGetFailover = vi.fn().mockResolvedValue({
  targets: {
    'llm-a': makeFailoverTarget(), 'llm-b': makeFailoverTarget(), transcriber: makeFailoverTarget(),
  },
  probes: {}, policy: { probeIntervalMinutes: 5, recoveryProbes: 3 }, events: [],
});
const mockResetPrompt = vi.fn();

vi.mock('../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
  updateSettings: vi.fn(),
  resetSettings: vi.fn(),
  resetPrompts: vi.fn(),
  resetPrompt: (...a: unknown[]) => mockResetPrompt(...a),
  getModels: vi.fn().mockResolvedValue([]),
  modelsQueryOptionsFor: (provider: string, slot: string) => ({
    queryKey: ['models', provider, slot],
    queryFn: () => mockGetModels(provider, slot),
  }),
  getWhisperModels: vi.fn().mockResolvedValue([]),
  getWhisperCapacity: vi.fn().mockResolvedValue({
    enabled: false, backend: 'openai-api', active: false, inactiveReason: 'disabled',
    capacity: 1, inFlight: 0, transcribingEpisodes: 0,
    maxEpisodes: { configured: 1, effective: 1 },
    chunkWorkers: { configured: 4, effective: 4 },
    worstCaseInFlight: 4, exceedsCapacity: false,
    health: { available: false },
  }),
  getSystemStatus: vi.fn().mockResolvedValue({}),
  runCleanup: vi.fn(),
  getProcessingEpisodes: vi.fn().mockResolvedValue([]),
  cancelProcessing: vi.fn(),
  setQueuePriority: vi.fn(),
  getOfflineQueueSettings: vi.fn().mockResolvedValue({
    enabled: false, ttlHours: 48, deferredCount: 0,
  }),
  updateOfflineQueueSettings: vi.fn(),
  getRateLimitHoldSettings: vi.fn().mockResolvedValue({
    enabled: false, holdUntil: null, llmUsageUrl: '', rateLimitProbeMinutes: 5,
  }),
  updateRateLimitHoldSettings: vi.fn(),
  refreshModels: vi.fn(),
  getRetention: vi.fn().mockResolvedValue({ retentionDays: 30, originalRetentionDays: 30, enabled: true }),
  updateRetention: vi.fn(),
  getProcessingTimeouts: vi.fn().mockResolvedValue({
    softTimeoutSeconds: 3600,
    hardTimeoutSeconds: 7200,
    defaults: { softTimeoutSeconds: 3600, hardTimeoutSeconds: 7200 },
    limits: { softMin: 60, hardMax: 86400 },
  }),
  updateProcessingTimeouts: vi.fn(),
  getAudioSettings: vi.fn().mockResolvedValue({ keepOriginalAudio: true }),
  updateAudioSettings: vi.fn(),
}));

vi.mock('../api/community', () => ({
  getReviewerSettings: vi.fn().mockResolvedValue({
    updatePatternsFromReviewerAdjustments: true, minTrimThreshold: 20, parallelAds: 4, parallelAdsDefault: 4,
  }),
  updateReviewerSettings: vi.fn(),
}));

vi.mock('../api/providers', () => ({
  listProviders: vi.fn().mockResolvedValue({}),
  updateProvider: vi.fn(),
  clearProvider: vi.fn(),
  testProvider: vi.fn(),
  testWhisperConnection: vi.fn(),
  testLlmConnection: vi.fn(),
  testSecondaryProviderConnection: vi.fn(),
  testPodcastIndex: vi.fn(),
}));

vi.mock('../api/failover', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/failover')>(),
  getFailover: (...a: unknown[]) => mockGetFailover(...a),
  triggerFailover: vi.fn(),
  cancelFailover: vi.fn(),
  probeFailover: vi.fn(),
  failoverQueryKey: ['failover'],
}));

vi.mock('../api/feeds', () => ({
  refreshAllArtwork: vi.fn(),
  feedsQueryOptions: { queryKey: ['feeds'], queryFn: vi.fn().mockResolvedValue({ feeds: [] }) },
}));

// Fields not set explicitly fall back to a neutral SettingValue so the
// page's generic hydration registry never dereferences undefined.
function sv(value: string, isDefault: boolean): SettingValue {
  return { value, isDefault };
}

function makeSettings(overrides: Partial<Record<string, unknown>> = {}): SettingsShape {
  const target: Record<string, unknown> = {
    systemPrompt: sv('custom system prompt', false),
    verificationPrompt: sv('default verification prompt', true),
    chapterPrompt: sv('default chapter prompt', true),
    reviewPrompt: sv('default review prompt', true),
    resurrectPrompt: sv('default resurrect prompt', true),
    systemPromptOverride: sv('', true),
    verificationPromptOverride: sv('', true),
    chapterPromptOverride: sv('', true),
    reviewPromptOverride: sv('', true),
    resurrectPromptOverride: sv('', true),
    ...overrides,
  };
  return new Proxy(target, {
    get(t, prop: string) {
      if (prop in t) return t[prop as keyof typeof t];
      return { value: '', isDefault: true };
    },
  }) as unknown as SettingsShape;
}

function makeClient() {
  return new QueryClient({
    // structuralSharing off: TanStack's default replaceEqualDeep would walk
    // the settings Proxy and rebuild it as a plain object, dropping the
    // fallback trap for every field the test doesn't override.
    defaultOptions: { queries: { retry: false, staleTime: 0, structuralSharing: false }, mutations: { retry: false } },
  });
}

function renderSettings(client = makeClient()) {
  return render(
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.removeItem('settings-section-failover');
  localStorage.setItem('settings-section-prompts', 'true');
  localStorage.setItem('settings-section-ad-reviewer', 'true');
  vi.clearAllMocks();
});

describe('Settings: per-prompt reset', () => {
  it('renders the per-field reset buttons disabled once every prompt is back at default', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({ systemPrompt: sv('default system prompt', true) }));
    renderSettings();
    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toBeDefined();
    });
    const resetButtons = screen.getAllByRole('button', { name: 'Reset' });
    expect(resetButtons.length).toBeGreaterThan(0);
    for (const btn of resetButtons) expect(btn).toHaveProperty('disabled', true);
  });

  it('fires resetPrompt("system") on the second click, not resetPrompts', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockResetPrompt.mockResolvedValue({ value: 'default system prompt', isDefault: true });
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toBeDefined();
    });

    const resetBtn = resetButtonFor('First Pass System Prompt');
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));

    expect(mockResetPrompt).toHaveBeenCalledWith('system');
  });

  it('re-seeds the textarea with the default text once the reset lands', async () => {
    mockGetSettings.mockResolvedValueOnce(makeSettings());
    mockResetPrompt.mockResolvedValue({ value: 'default system prompt', isDefault: true });
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toHaveProperty('value', 'custom system prompt');
    });

    // The refetch after the mutation returns the field already at default.
    mockGetSettings.mockResolvedValue(makeSettings({ systemPrompt: sv('default system prompt', true) }));

    const resetBtn = resetButtonFor('First Pass System Prompt');
    await user.click(resetBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));

    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toHaveProperty('value', 'default system prompt');
    });
  });

  it('fires resetPrompt("review") and resetPrompt("resurrect") from the Ad Reviewer section', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      systemPrompt: sv('default system prompt', true),
      reviewPrompt: sv('custom review prompt', false),
      resurrectPrompt: sv('custom resurrect prompt', false),
    }));
    mockResetPrompt.mockResolvedValue({ value: 'default', isDefault: true });
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByLabelText('Review prompt (confirm / adjust / reject)')).toBeDefined();
    });

    const reviewBtn = resetButtonFor('Review prompt (confirm / adjust / reject)');
    const resurrectBtn = resetButtonFor('Resurrect prompt (resurrect / reject)');

    await user.click(reviewBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(mockResetPrompt).toHaveBeenCalledWith('review');

    await user.click(resurrectBtn);
    await user.click(screen.getByRole('button', { name: 'Click again to confirm' }));
    expect(mockResetPrompt).toHaveBeenCalledWith('resurrect');
  });
});

describe('Settings: Ad Reviewer placement', () => {
  // Guards the move out of Experiments: order, not index, so an unrelated
  // section landing between these headings does not fail the test.
  function precedes(a: HTMLElement, b: HTMLElement) {
    return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  }

  it('renders Ad Reviewer under AI & Processing, before Seed sponsors and before Experiments', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSettings();

    const adReviewer = await screen.findByRole('heading', { name: 'Ad Reviewer' });
    const seedSponsors = screen.getByRole('heading', { name: 'Seed sponsors' });
    const aiHeader = screen.getByRole('heading', { name: 'AI & Processing' });

    expect(precedes(aiHeader, adReviewer)).toBe(true);
    expect(precedes(adReviewer, seedSponsors)).toBe(true);

    const experiments = screen.getByRole('heading', { name: 'Experiments' });
    expect(precedes(adReviewer, experiments)).toBe(true);
  });
});

describe('Settings: Pattern cleanup placement', () => {
  it('renders the Pattern cleanup card under Experiments, before Output', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSettings();

    const card = await screen.findByTestId('pattern-cleanup-section');
    const experiments = screen.getByRole('heading', { name: 'Experiments' });
    const output = screen.getByRole('heading', { name: 'Output' });
    expect(Boolean(experiments.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    expect(Boolean(card.compareDocumentPosition(output) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });
});

describe('Settings: Failover placement', () => {
  function precedes(a: HTMLElement, b: HTMLElement) {
    return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  }

  it('renders the Failover card after AI Models and before LLM Tunables', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSettings();

    const failover = await screen.findByTestId('failover-section');
    expect(precedes(screen.getByTestId('ai-models-section'), failover)).toBe(true);
    expect(precedes(failover, screen.getByTestId('stage-tunables-section'))).toBe(true);
  });

  it('fetches failover state only once the card is opened', async () => {
    localStorage.removeItem('settings-section-failover');
    mockGetFailover.mockClear();
    mockGetSettings.mockResolvedValue(makeSettings());
    const user = userEvent.setup();
    renderSettings();

    await screen.findByTestId('failover-section');
    expect(mockGetFailover).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /^Failover/ }));
    await waitFor(() => expect(mockGetFailover).toHaveBeenCalledTimes(1));
  });

  it('defers the standby catalog until opening and keeps cached models on reopening', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      failoverLlmEnabled: { value: true, isDefault: false },
      failoverLlmProvider: sv('ollama', false),
    }));
    const client = makeClient();
    client.setQueryDefaults(['models'], { staleTime: Infinity });
    const user = userEvent.setup();
    renderSettings(client);
    await screen.findByLabelText('First Pass System Prompt');
    expect(mockGetModels.mock.calls.filter(([, slot]) => slot === 'failover')).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: /^Failover/ }));
    await waitFor(() => expect(mockGetModels).toHaveBeenCalledWith('ollama', 'failover'));
    expect(client.getQueryData(['models', 'ollama', 'failover'])).toEqual([]);

    await user.click(screen.getByRole('button', { name: /^Failover/ }));
    await user.click(screen.getByRole('button', { name: /^Failover/ }));
    expect(mockGetModels.mock.calls.filter(([, slot]) => slot === 'failover')).toHaveLength(1);
  });

  it('fetches search-revealed standby models and disables hidden card queries', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      failoverLlmEnabled: { value: true, isDefault: false },
      failoverLlmProvider: sv('ollama', false),
    }));
    const client = makeClient();
    const user = userEvent.setup();
    renderSettings(client);
    await screen.findByLabelText('First Pass System Prompt');
    const key = ['models', 'ollama', 'failover'];
    expect(client.getQueryCache().find({ queryKey: key })?.isActive()).toBe(false);

    const search = screen.getByRole('textbox', { name: 'Search settings' });
    await user.type(search, 'failover');
    await waitFor(() => expect(mockGetModels).toHaveBeenCalledWith('ollama', 'failover'));
    expect(client.getQueryCache().find({ queryKey: key })?.isActive()).toBe(true);

    await user.clear(search);
    expect(client.getQueryCache().find({ queryKey: key })?.isActive()).toBe(false);
    await user.click(screen.getByRole('button', { name: /^Failover/ }));
    expect(client.getQueryCache().find({ queryKey: key })?.isActive()).toBe(true);
    await user.type(search, 'resurrect prompt');
    expect(client.getQueryCache().find({ queryKey: key })?.isActive()).toBe(false);
    expect(client.getQueryCache().find({ queryKey: ['failover'] })?.isActive()).toBe(false);

    await user.clear(search);
    expect(client.getQueryCache().find({ queryKey: key })?.isActive()).toBe(true);
  });
});

describe('Settings: standby upload attempts', () => {
  it('saves an independent standby limit while the active backend is local', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      whisperBackend: sv('local', false),
      whisperMaxAttempts: { value: 3, isDefault: false },
      failoverWhisperEnabled: { value: true, isDefault: false },
      failoverWhisperBackend: sv('openai-api', false),
      failoverWhisperMaxAttempts: { value: null, isDefault: true },
    }));
    const user = userEvent.setup();
    renderSettings();
    await screen.findByLabelText('First Pass System Prompt');
    await user.click(screen.getByRole('button', { name: /^Failover/ }));
    const field = screen.getByLabelText('Max upload attempts') as HTMLInputElement;
    expect(field.value).toBe('');
    expect(field.placeholder).toBe('3');
    await user.type(field, '5');
    await user.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith(
      expect.objectContaining({ failoverWhisperMaxAttempts: 5 }),
    ));
  });

  it('shows a rejected standby limit and keeps the edit available to correct', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      whisperBackend: sv('local', false),
      failoverWhisperEnabled: { value: true, isDefault: false },
      failoverWhisperBackend: sv('openai-api', false),
      failoverWhisperMaxAttempts: { value: null, isDefault: true },
    }));
    vi.mocked(updateSettings).mockRejectedValueOnce(new Error('failoverWhisperMaxAttempts must be between 1 and 10'));
    const user = userEvent.setup();
    renderSettings();
    await screen.findByLabelText('First Pass System Prompt');
    await user.click(screen.getByRole('button', { name: /^Failover/ }));
    const field = screen.getByLabelText('Max upload attempts') as HTMLInputElement;
    await user.type(field, '11');
    await user.click(screen.getByRole('button', { name: 'Save Changes' }));
    await screen.findByText('failoverWhisperMaxAttempts must be between 1 and 10');
    expect(field.value).toBe('11');
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDefined();
  });
});

describe('Settings: Reset All copy', () => {
  it('warns that model choices are cleared by the bulk ad-detection reset', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    const user = userEvent.setup();
    renderSettings();

    await waitFor(() => {
      expect(screen.getByLabelText('First Pass System Prompt')).toBeDefined();
    });

    // Editing a field flips hasChanges, which is what shows the sticky
    // save bar holding the "Reset All" button.
    await user.type(screen.getByLabelText('First Pass System Prompt'), ' edited');

    const resetAllBtn = await screen.findByRole('button', { name: 'Reset All' });
    expect(resetAllBtn.getAttribute('title')).toBe(
      'Also clears your AI model choices; you may need to pick them again.'
    );
  });
});

describe('Settings loading placeholder', () => {
  it('shows header and row skeletons while the settings query is pending', () => {
    mockGetSettings.mockReturnValueOnce(new Promise(() => {}));
    renderSettings();
    expect(screen.getByTestId('skeleton-page-header')).toBeTruthy();
    expect(screen.getByTestId('skeleton-rows')).toBeTruthy();
  });

  it('drops the skeletons once the settings land', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSettings();
    await screen.findByLabelText('First Pass System Prompt');
    expect(screen.queryByTestId('skeleton-page-header')).toBeNull();
  });
});

describe('Settings system status polling', () => {
  it('polls faster while a Podping node check is unfinished', () => {
    const withCheck = (status: 'pending' | 'running' | 'completed') => ({
      podping: {
        listenerEnabled: true,
        allNodesDown: false,
        degradedSince: null,
        nodes: [],
        check: {
          checkId: 'check-1', status,
          requestedAt: '2026-09-20T00:00:00Z', startedAt: null, completedAt: null,
        },
      },
    }) as unknown as SystemStatus;

    expect(systemStatusRefetchInterval(withCheck('pending'))).toBe(2_000);
    expect(systemStatusRefetchInterval(withCheck('running'))).toBe(2_000);
    expect(systemStatusRefetchInterval(withCheck('completed'))).toBe(30_000);
    expect(systemStatusRefetchInterval()).toBe(30_000);
  });
});

describe('Settings search', () => {
  function card(heading: string) {
    return screen.getByRole('heading', { name: heading }).closest('[data-search-key]') as HTMLElement;
  }

  it('filters cards, shows the empty state, disables bulk buttons, and clear restores', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    const user = userEvent.setup();
    renderSettings();
    await screen.findByRole('heading', { name: 'Ad Reviewer' });

    const search = screen.getByRole('textbox', { name: 'Search settings' });
    await user.type(search, 'resurrect prompt');
    expect(card('Ad Reviewer').className).not.toContain('hidden');
    expect(card('Seed sponsors').className).toContain('hidden');
    expect(screen.getByRole('button', { name: 'Expand all' })).toHaveProperty('disabled', true);
    expect(screen.getByRole('button', { name: 'Collapse all' })).toHaveProperty('disabled', true);

    await user.clear(search);
    await user.type(search, 'zzz-no-such-setting');
    expect(screen.getByText('No settings match "zzz-no-such-setting".')).toBeDefined();

    await user.click(screen.getByRole('button', { name: 'Clear settings search' }));
    expect(screen.queryByText(/No settings match/)).toBeNull();
    expect(card('Seed sponsors').className).not.toContain('hidden');
    expect(screen.getByRole('button', { name: 'Expand all' })).toHaveProperty('disabled', false);
  });
});
