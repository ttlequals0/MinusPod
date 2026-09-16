/**
 * Regression tests for the secondary provider type on the Settings page: the
 * value the LLM Provider section shows, the value the model-catalog queries
 * use, and the value Save writes must all be the same one. A display-only
 * fallback diverged from the saved value, so the form never settled clean and
 * stopped re-hydrating.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Settings from './Settings';
import type { Settings as SettingsShape, SettingValue } from '../api/types';
import type { ModelCatalog } from '../hooks/useModelCatalog';
import type { ModelsRefresh } from '../hooks/useModelsRefresh';
import * as settingsApi from '../api/settings';

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

// Latest props handed to the two sections under test, so assertions read the
// real values Settings passes down rather than a re-implementation of them.
const captured = vi.hoisted(() => ({
  llm: {} as Record<string, unknown>,
  models: {} as Record<string, unknown>,
  reviewer: {} as Record<string, unknown>,
}));

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
vi.mock('./settings/LLMProviderSection', () => ({
  default: (props: Record<string, unknown>) => { captured.llm = props; return null; },
}));
vi.mock('./settings/AIModelsSection', () => ({
  default: (props: Record<string, unknown>) => { captured.models = props; return null; },
}));
vi.mock('./settings/AdReviewerSection', () => ({
  default: (props: Record<string, unknown>) => { captured.reviewer = props; return null; },
}));
vi.mock('./settings/StageTunablesSection', () => ({ default: () => null }));
vi.mock('./settings/TranscriptionSection', () => ({ default: () => null }));
vi.mock('./settings/AudioSection', () => ({ default: () => null }));
vi.mock('./settings/CoverArtSection', () => ({ default: () => null }));
vi.mock('./settings/AdDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/GlobalDefaultsSection', () => ({ default: () => null }));
vi.mock('./settings/SegmentActionsSection', () => ({ default: () => null }));
vi.mock('./settings/Podcasting20Section', () => ({ default: () => null }));
vi.mock('./settings/AudioCueDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/PositionalPriorSection', () => ({ default: () => null }));
vi.mock('./settings/CommunityPatternsSection', () => ({ default: () => null }));
vi.mock('./settings/DatabaseBackupSection', () => ({ default: () => null }));
vi.mock('./settings/QueueControlSection', () => ({ default: () => null }));
vi.mock('./settings/TranscriptNormalizationSection', () => ({ default: () => null }));

const mockGetSettings = vi.fn();
const mockGetModels = vi.fn();
const mockUpdateSettings = vi.fn().mockResolvedValue({ message: 'ok' });

vi.mock('../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
  updateSettings: (...a: unknown[]) => mockUpdateSettings(...a),
  resetSettings: vi.fn(),
  resetPrompts: vi.fn(),
  resetPrompt: vi.fn(),
  getModels: (...a: unknown[]) => mockGetModels(...a),
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

vi.mock('../api/feeds', () => ({
  refreshAllArtwork: vi.fn(),
}));

function sv(value: string, isDefault: boolean): SettingValue {
  return { value, isDefault };
}

function makeSettings(overrides: Partial<Record<string, unknown>> = {}): SettingsShape {
  const target: Record<string, unknown> = {
    systemPrompt: sv('', true),
    verificationPrompt: sv('', true),
    chapterPrompt: sv('', true),
    reviewPrompt: sv('', true),
    resurrectPrompt: sv('', true),
    systemPromptOverride: sv('', true),
    verificationPromptOverride: sv('', true),
    chapterPromptOverride: sv('', true),
    reviewPromptOverride: sv('', true),
    resurrectPromptOverride: sv('', true),
    llmProvider: sv('anthropic', false),
    secondaryProviderEnabled: { value: false, isDefault: true },
    secondaryProvider: sv('', true),
    secondaryProviderBaseUrl: sv('', true),
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
    defaultOptions: { queries: { retry: false, staleTime: 0, structuralSharing: false }, mutations: { retry: false } },
  });
}

function renderSettings(client: QueryClient = makeClient()) {
  return render(
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  captured.llm = {};
  captured.models = {};
  captured.reviewer = {};
  mockGetModels.mockResolvedValue([]);
  mockUpdateSettings.mockResolvedValue({ message: 'ok' });
});

describe('Settings: secondary provider with no persisted type', () => {
  const enabledWithoutType = {
    secondaryProviderEnabled: { value: true, isDefault: false },
    secondaryProvider: sv('', true),
    detectionProvider: sv('secondary', false),
  };

  it('hydrates clean instead of showing a type the server never saved', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(enabledWithoutType));
    renderSettings();

    await waitFor(() => expect(captured.llm.secondaryProviderEnabled).toBe(true));
    expect(captured.llm.secondaryProvider).toBe('');
    expect(mockGetModels).not.toHaveBeenCalledWith(expect.anything(), 'secondary');
    expect(screen.queryByRole('button', { name: 'Save Changes' })).toBeNull();
  });

  it('runs a stage on the primary catalog while the secondary has no type', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(enabledWithoutType));
    renderSettings();

    await waitFor(() => expect(mockGetModels).toHaveBeenCalledWith('anthropic', 'primary'));
    expect(mockGetModels).not.toHaveBeenCalledWith(expect.anything(), 'secondary');
    expect(captured.llm.secondaryProvider).toBe('');
  });

  it('rebuilds the primary client when the reviewer is on an untyped secondary', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      ...enabledWithoutType,
      reviewProvider: sv('secondary', false),
    }));
    vi.mocked(settingsApi.refreshModels).mockResolvedValue({ models: [], count: 0 });
    renderSettings();

    await waitFor(() => expect(captured.reviewer.modelsRefresh).toBeTruthy());
    await act(async () => { reviewerRefresh().refresh(); });

    await waitFor(() => {
      expect(vi.mocked(settingsApi.refreshModels)).toHaveBeenCalledWith('primary');
    });
    expect(vi.mocked(settingsApi.refreshModels)).not.toHaveBeenCalledWith('secondary');
  });

  it('re-hydrates the type from a later server update', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(enabledWithoutType));
    const client = makeClient();
    renderSettings(client);

    await waitFor(() => expect(captured.llm.secondaryProviderEnabled).toBe(true));

    mockGetSettings.mockResolvedValue(makeSettings({
      ...enabledWithoutType,
      secondaryProvider: sv('openrouter', false),
    }));
    await act(async () => { await client.invalidateQueries({ queryKey: ['settings'] }); });

    await waitFor(() => expect(captured.llm.secondaryProvider).toBe('openrouter'));
  });

  it('leaves the type unset, and the form clean, while the secondary provider is off', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSettings();

    await waitFor(() => {
      expect(captured.llm.secondaryProvider).toBe('');
    });
    expect(screen.queryByRole('button', { name: 'Save Changes' })).toBeNull();
  });
});

function stageCatalog(prop: string): Partial<ModelCatalog> {
  return (captured.models[prop] as ModelCatalog | undefined) ?? {};
}

function reviewCatalog(): Partial<ModelCatalog> {
  return (captured.reviewer.catalog as ModelCatalog | undefined) ?? {};
}

function stagesRefresh(): ModelsRefresh {
  return captured.models.modelsRefresh as ModelsRefresh;
}

function reviewerRefresh(): ModelsRefresh {
  return captured.reviewer.modelsRefresh as ModelsRefresh;
}

describe('Settings: stage catalog fetch state', () => {
  it('passes each stage catalog\'s own loading flag to the AI Models section', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockGetModels.mockReturnValue(new Promise(() => {}));
    renderSettings();

    await waitFor(() => {
      expect(stageCatalog('verificationCatalog').isLoading).toBe(true);
    });
    expect(stageCatalog('chaptersCatalog').isLoading).toBe(true);
    expect(stageCatalog('detectionCatalog').isLoading).toBe(true);
  });

  it('passes each stage catalog\'s own error flag to the AI Models section', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    mockGetModels.mockRejectedValue(new Error('catalog unreachable'));
    renderSettings();

    await waitFor(() => {
      expect(stageCatalog('verificationCatalog').isError).toBe(true);
    });
    expect(stageCatalog('chaptersCatalog').isError).toBe(true);
    expect(stageCatalog('detectionCatalog').isError).toBe(true);
  });
});

describe('Settings: review catalog fetch state', () => {
  const secondaryReview = {
    secondaryProviderEnabled: { value: true, isDefault: false },
    secondaryProvider: sv('openrouter', false),
    reviewProvider: sv('secondary', false),
  };

  it('passes the review catalog loading and error flags to the reviewer section', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(secondaryReview));
    mockGetModels.mockReturnValue(new Promise(() => {}));
    renderSettings();

    await waitFor(() => expect(reviewCatalog().isLoading).toBe(true));

    mockGetModels.mockRejectedValue(new Error('catalog unreachable'));
    renderSettings();
    await waitFor(() => expect(reviewCatalog().isError).toBe(true));
  });

  it('refreshes the reviewer\'s own slot, not the primary client', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(secondaryReview));
    vi.mocked(settingsApi.refreshModels).mockResolvedValue({ models: [], count: 0 });
    renderSettings();

    await waitFor(() => expect(captured.reviewer.modelsRefresh).toBeTruthy());
    await act(async () => { reviewerRefresh().refresh(); });

    await waitFor(() => {
      expect(vi.mocked(settingsApi.refreshModels)).toHaveBeenCalledWith('secondary');
    });
  });

  it('refetches the detection catalog the review select borrows', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({ reviewProvider: sv('same_as_pass', false) }));
    vi.mocked(settingsApi.refreshModels).mockResolvedValue({ models: [], count: 0 });
    renderSettings();

    await waitFor(() => expect(captured.reviewer.modelsRefresh).toBeTruthy());
    const detectionCalls = () => mockGetModels.mock.calls
      .filter((c) => c[0] === 'anthropic' && c[1] === 'primary').length;
    await waitFor(() => expect(detectionCalls()).toBeGreaterThan(0));
    const before = detectionCalls();

    await act(async () => { reviewerRefresh().refresh(); });

    await waitFor(() => expect(detectionCalls()).toBeGreaterThan(before));
  });

  it('keeps the stage Refresh button pending while a review refresh runs', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({
      secondaryProviderEnabled: { value: true, isDefault: false },
      secondaryProvider: sv('openrouter', false),
      detectionProvider: sv('secondary', false),
      verificationProvider: sv('primary', false),
      chaptersProvider: sv('primary', false),
      reviewProvider: sv('primary', false),
    }));
    let releaseSecondary = () => {};
    vi.mocked(settingsApi.refreshModels).mockImplementation((slot) => (slot === 'secondary'
      ? new Promise((resolve) => { releaseSecondary = () => resolve({ models: [], count: 0 }); })
      : Promise.resolve({ models: [], count: 0 })));
    renderSettings();

    await waitFor(() => expect(captured.models.modelsRefresh).toBeTruthy());
    await act(async () => { stagesRefresh().refresh(); });
    await waitFor(() => expect(stagesRefresh().isPending).toBe(true));

    await act(async () => { reviewerRefresh().refresh(); });
    // The review refresh has settled; the stage refresh has not.
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0); }); });

    expect(reviewerRefresh().isPending).toBe(false);
    expect(stagesRefresh().isPending).toBe(true);
    await act(async () => { releaseSecondary(); });
    await waitFor(() => expect(stagesRefresh().isPending).toBe(false));
  });

  it('asks for the primary slot when the reviewer runs on the primary provider', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({ reviewProvider: sv('primary', false) }));
    vi.mocked(settingsApi.refreshModels).mockResolvedValue({ models: [], count: 0 });
    renderSettings();

    await waitFor(() => expect(captured.reviewer.modelsRefresh).toBeTruthy());
    await act(async () => { reviewerRefresh().refresh(); });

    await waitFor(() => {
      expect(vi.mocked(settingsApi.refreshModels)).toHaveBeenCalledWith('primary');
    });
  });

  it('surfaces a failed refresh on the reviewer section', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(secondaryReview));
    vi.mocked(settingsApi.refreshModels).mockRejectedValue(new Error('No secondary provider configured'));
    renderSettings();

    await waitFor(() => expect(captured.reviewer.modelsRefresh).toBeTruthy());
    await act(async () => { reviewerRefresh().refresh(); });

    await waitFor(() => {
      expect(reviewerRefresh().error).toBe('No secondary provider configured');
    });
  });
});

describe('Settings: stage catalog refresh', () => {
  const mixedSlots = {
    secondaryProviderEnabled: { value: true, isDefault: false },
    secondaryProvider: sv('openrouter', false),
    detectionProvider: sv('secondary', false),
    verificationProvider: sv('primary', false),
  };

  it('refreshes every slot the three stages resolve to, once each', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(mixedSlots));
    vi.mocked(settingsApi.refreshModels).mockResolvedValue({ models: [], count: 0 });
    renderSettings();

    await waitFor(() => expect(captured.models.modelsRefresh).toBeTruthy());
    await act(async () => { stagesRefresh().refresh(); });

    await waitFor(() => {
      expect(vi.mocked(settingsApi.refreshModels)).toHaveBeenCalledTimes(2);
    });
    const slots = vi.mocked(settingsApi.refreshModels).mock.calls.map((c) => c[0]);
    expect(new Set(slots)).toEqual(new Set(['primary', 'secondary']));
  });

  it('keeps a failed stage refresh out of the reviewer section', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(mixedSlots));
    vi.mocked(settingsApi.refreshModels).mockRejectedValue(new Error('provider refused'));
    renderSettings();

    await waitFor(() => expect(captured.models.modelsRefresh).toBeTruthy());
    await act(async () => { stagesRefresh().refresh(); });

    await waitFor(() => expect(stagesRefresh().error).toBe('provider refused'));
    expect(reviewerRefresh().error).toBeNull();
  });
});
