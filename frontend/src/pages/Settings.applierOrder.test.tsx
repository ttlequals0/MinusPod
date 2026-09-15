/**
 * A save sends one PUT: the backend applies the secondary provider fields
 * before the primary provider fields within that request, so a combined
 * change to llmProvider and the secondary provider does not need the
 * frontend to split it into two writes.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Settings from './Settings';
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
// Exposes the two form controls this test drives, wired to the real
// callback props Settings.tsx passes down, so clicking them mutates the
// page's actual state instead of a re-implementation of it.
vi.mock('./settings/LLMProviderSection', () => ({
  default: (props: {
    onProviderChange: (provider: string) => void;
    onSecondaryProviderEnabledChange: (enabled: boolean) => void;
  }) => (
    <div>
      <button onClick={() => props.onProviderChange('openai-compatible')}>change-primary-provider</button>
      <button onClick={() => props.onSecondaryProviderEnabledChange(true)}>enable-secondary</button>
    </div>
  ),
}));
vi.mock('./settings/AIModelsSection', () => ({ default: () => null }));
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
const mockUpdateSettings = vi.fn().mockResolvedValue({ message: 'ok' });

vi.mock('../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
  updateSettings: (...a: unknown[]) => mockUpdateSettings(...a),
  resetSettings: vi.fn(),
  resetPrompts: vi.fn(),
  resetPrompt: vi.fn(),
  getModels: vi.fn().mockResolvedValue([]),
  modelsQueryOptionsFor: (provider: string, slot: string) => ({
    queryKey: ['models', provider, slot],
    queryFn: () => Promise.resolve([]),
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

function renderSettings() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <Settings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUpdateSettings.mockResolvedValue({ message: 'ok' });
});

describe('Settings: one PUT per save', () => {
  it('sends the secondary provider fields and llmProvider in a single request', async () => {
    mockGetSettings.mockResolvedValue(makeSettings());
    const user = userEvent.setup();
    renderSettings();

    await screen.findByText('change-primary-provider');

    // Enabling secondary seeds a concrete type, so this also dirties
    // secondaryProvider alongside secondaryProviderEnabled.
    await user.click(screen.getByText('enable-secondary'));
    await user.click(screen.getByText('change-primary-provider'));

    const saveButton = await screen.findByRole('button', { name: 'Save Changes' });
    await user.click(saveButton);

    await waitFor(() => expect(mockUpdateSettings).toHaveBeenCalledTimes(1));

    expect(mockUpdateSettings.mock.calls[0][0]).toEqual({
      secondaryProviderEnabled: true,
      secondaryProvider: 'anthropic',
      llmProvider: 'openai-compatible',
    });
  });
});
