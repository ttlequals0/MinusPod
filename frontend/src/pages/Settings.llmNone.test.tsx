/** Settings with the None provider: sections follow the saved llmEnabled flag and stored model fields are never rewritten. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Settings from './Settings';
import { useLlmEnabled } from '../hooks/useLlmEnabled';
import type { Settings as SettingsShape, SettingValue } from '../api/types';

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

// Latest props handed to the sections under test, plus the flag each section reads itself.
const captured = vi.hoisted(() => ({
  llm: {} as Record<string, unknown>,
  models: {} as Record<string, unknown>,
  reviewer: {} as Record<string, unknown>,
  prompts: {} as Record<string, unknown>,
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
  default: function Probe(props: Record<string, unknown>) { captured.llm = { ...props, llmEnabled: useLlmEnabled() }; return null; },
}));
vi.mock('./settings/AIModelsSection', () => ({
  default: function Probe(props: Record<string, unknown>) { captured.models = { ...props, llmEnabled: useLlmEnabled() }; return null; },
}));
vi.mock('./settings/AdReviewerSection', () => ({
  default: function Probe(props: Record<string, unknown>) { captured.reviewer = { ...props, llmEnabled: useLlmEnabled() }; return null; },
}));
vi.mock('./settings/StageTunablesSection', () => ({ default: () => null }));
vi.mock('./settings/SeedSponsorsSection', () => ({ default: () => null }));
vi.mock('./settings/ExperimentsSection', () => ({ default: () => null }));
vi.mock('./settings/PromptsSection', () => ({
  default: function Probe(props: Record<string, unknown>) { captured.prompts = { ...props, llmEnabled: useLlmEnabled() }; return null; },
}));
vi.mock('./settings/TranscriptionSection', () => ({ default: () => null }));
vi.mock('./settings/AudioSection', () => ({ default: () => null }));
vi.mock('./settings/CoverArtSection', () => ({ default: () => null }));
vi.mock('./settings/AdDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/GlobalDefaultsSection', () => ({ default: () => null }));
vi.mock('./settings/SegmentActionsSection', () => ({ default: () => null }));
vi.mock('./settings/Podcasting20Section', () => ({ default: () => null }));
vi.mock('./settings/AudioCueDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/PositionalPriorSection', () => ({ default: () => null }));
vi.mock('./settings/PatternCleanupSection', () => ({ default: () => null }));
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
  generateSbUserId: vi.fn(),
  getSbUserId: vi.fn(),
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
  feedsQueryOptions: { queryKey: ['feeds'], queryFn: vi.fn().mockResolvedValue({ feeds: [] }) },
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
    llmEnabled: true,
    claudeModel: sv('claude-sonnet', false),
    verificationModel: sv('claude-haiku', false),
    chaptersModel: sv('claude-haiku', false),
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
  captured.prompts = {};
  mockGetModels.mockResolvedValue([]);
  mockUpdateSettings.mockResolvedValue({ message: 'ok' });
});


describe('Settings: None provider', () => {
  const savedNone = { llmProvider: sv('none', false), llmEnabled: false };

  it('disables every model section from the saved flag and fetches no model lists', async () => {
    mockGetSettings.mockResolvedValue(makeSettings(savedNone));
    renderSettings();

    await waitFor(() => expect(captured.models.llmEnabled).toBe(false));
    expect(captured.llm.llmEnabled).toBe(false);
    expect(captured.reviewer.llmEnabled).toBe(false);
    expect(captured.prompts.llmEnabled).toBe(false);
    expect(mockGetModels).not.toHaveBeenCalled();
  });

  it('keeps the sections enabled until the saved response says the provider is None', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSettings();
    await waitFor(() => expect(captured.models.selectedModel).toBe('claude-sonnet'));

    act(() => { (captured.llm.onProviderChange as (p: string) => void)('none'); });

    await waitFor(() => expect(captured.models.selectedModel).toBe('claude-sonnet'));
    expect(captured.models.llmEnabled).toBe(true);
    expect(captured.reviewer.llmEnabled).toBe(true);
  });

  it('saves None as the provider alone and leaves the stored models out of the payload', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    renderSettings();
    await waitFor(() => expect(captured.llm.llmEnabled).toBe(true));
    await waitFor(() => expect(captured.models.selectedModel).toBe('claude-sonnet'));

    act(() => { (captured.llm.onProviderChange as (p: string) => void)('none'); });
    mockGetSettings.mockResolvedValue(makeSettings(savedNone));
    fireEvent.click(await screen.findByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledTimes(1));
    expect(mockUpdateSettings).toHaveBeenCalledWith({ llmProvider: 'none' });
    await waitFor(() => expect(captured.models.llmEnabled).toBe(false));
    expect(captured.models.selectedModel).toBe('claude-sonnet');
  });

  it('switching back from None changes the provider only and re-enables from the response', async () => {
    mockGetSettings.mockResolvedValue(makeSettings({ ...savedNone, claudeModel: sv('claude-sonnet', false) }));
    renderSettings();
    await waitFor(() => expect(captured.llm.llmProvider).toBe('none'));
    await act(async () => { await Promise.resolve(); });

    act(() => { (captured.llm.onProviderChange as (p: string) => void)('anthropic'); });
    expect(captured.models.llmEnabled).toBe(false);
    expect(captured.models.selectedModel).toBe('claude-sonnet');

    mockGetSettings.mockResolvedValue(makeSettings());
    fireEvent.click(await screen.findByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith({ llmProvider: 'anthropic' }));
    await waitFor(() => expect(captured.models.llmEnabled).toBe(true));
  });

  it('keeps audio fingerprinting editable and saves it while the provider is None', async () => {
    localStorage.setItem('settings-section-audio-fingerprinting', 'true');
    mockGetSettings.mockResolvedValue(makeSettings({
      ...savedNone,
      fingerprintEnabled: { value: true, isDefault: true },
      fingerprintMatchStrictness: { value: 0.65, isDefault: true },
      fingerprintMinCoverage: { value: 0.5, isDefault: true },
      fingerprintGapToleranceS: { value: 3.5, isDefault: true },
      fingerprintMinAdLengthS: { value: 5, isDefault: true },
    }));
    renderSettings();
    const toggle = await screen.findByRole('switch', { name: 'Enable audio fingerprinting' }) as HTMLButtonElement;
    await waitFor(() => expect(captured.models.llmEnabled).toBe(false));
    expect(toggle.hasAttribute('disabled')).toBe(false);
    expect(toggle.closest('fieldset:disabled')).toBeNull();
    const strictness = screen.getByLabelText('Match strictness') as HTMLInputElement;
    expect(strictness.disabled).toBe(false);
    expect(strictness.closest('fieldset:disabled')).toBeNull();

    fireEvent.change(strictness, { target: { value: '0.7' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledWith({ fingerprintMatchStrictness: 0.7 }));
  });
});
