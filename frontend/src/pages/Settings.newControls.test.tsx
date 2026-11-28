/** Settings page with the real 2.103 sections: cross-category search and the sticky save bar. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../context/ThemeContext';
import Settings from './Settings';
import type { Settings as SettingsShape, SettingValue } from '../api/types';
import { makeFailoverTarget } from '../test/failover';
import { updateSettings } from '../api/settings';

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
vi.mock('./settings/DataManagementSection', () => ({ default: () => null }));
vi.mock('./settings/DatabaseStatsSection', () => ({ default: () => null }));
vi.mock('./settings/NotificationsSection', () => ({ default: () => null }));
vi.mock('./settings/AuthenticatedFeedsSection', () => ({ default: () => null }));
vi.mock('./settings/ProcessingQueueSection', () => ({ default: () => null }));
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
  generateSbUserId: vi.fn(),
  getSbUserId: vi.fn(),
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
  getRetention: vi.fn().mockResolvedValue({
    retentionDays: 30, originalRetentionDays: 30, enabled: true, unfetchedRetentionDays: 14, fetchedRetentionGraceDays: 3,
  }),
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

vi.mock('../api/apiTokens', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/apiTokens')>(),
  listApiTokens: vi.fn().mockResolvedValue([
    { id: 1, name: 'home-assistant', prefix: 'f3a9c1d2', createdAt: '2026-01-01T00:00:00Z', lastUsedAt: null },
  ]),
}));

vi.mock('../api/podping', () => ({
  podpingNodesQueryKey: ['podping', 'nodes'],
  getPodpingNodes: vi.fn().mockResolvedValue({ nodes: [], defaults: [] }),
  updatePodpingNodes: vi.fn(),
  resetPodpingNodes: vi.fn(),
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
      <ThemeProvider><Settings /></ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.removeItem('settings-section-failover');
  localStorage.setItem('settings-section-prompts', 'true');
  localStorage.setItem('settings-section-ad-reviewer', 'true');
  vi.clearAllMocks();
});


function panel(container: HTMLElement, id: string) {
  return container.querySelector(`[data-settings-category="${id}"]`) as HTMLElement;
}

describe('Settings: 2.103 controls in the category layout', () => {
  beforeEach(() => {
    localStorage.setItem('sideCutout', 'fill');
    mockGetSettings.mockResolvedValue(makeSettings({
      whisperBackend: sv('openai-api', false),
      transcribeRawApiChunks: sv(false as unknown as string, true),
      podpingEnabled: sv(false as unknown as string, true),
      adaptiveRefreshEnabled: sv(true as unknown as string, true),
    }));
  });

  it('puts Global defaults under General, not AI and transcription', async () => {
    const { container } = renderSettings();
    const heading = await screen.findByRole('heading', { name: 'Global defaults' });
    expect(panel(container, 'general').contains(heading)).toBe(true);
    expect(panel(container, 'ai').contains(heading)).toBe(false);
    const cards = Array.from(panel(container, 'general').querySelectorAll('[data-search-key]'))
      .filter((c) => !c.parentElement?.closest('[data-search-key]')).length;
    expect(screen.getAllByRole('button', { name: new RegExp(`^General \\(${cards}\\)`) }).length).toBeGreaterThan(0);
  });

  it.each([
    ['token', 'data'],
    ['revoke', 'data'],
    ['never-downloaded', 'data'],
    ['adaptive', 'general'],
    ['dormant', 'general'],
    ['cutout', 'general'],
    ['splice', 'detection'],
  ])('search for "%s" reveals its section from another category', async (term, category) => {
    const user = userEvent.setup();
    const { container } = renderSettings();
    await screen.findByRole('heading', { name: 'Global defaults' });
    await user.click(screen.getAllByRole('button', { name: /^Output/ })[0]);
    expect(panel(container, category).className).toContain('hidden');

    await user.type(screen.getByRole('textbox', { name: 'Search settings' }), term);
    expect(panel(container, category).className).not.toContain('hidden');
    const shown = Array.from(panel(container, category).querySelectorAll<HTMLElement>('[data-search-key]'))
      .filter((el) => !el.className.includes('hidden') && (el.textContent ?? '').toLowerCase().includes(term.toLowerCase()));
    expect(shown.length).toBeGreaterThan(0);
  });

  it('shows the save bar for an adaptive refresh draft made while another category is active', async () => {
    const user = userEvent.setup();
    renderSettings();
    const toggle = await screen.findByRole('switch', { name: 'Adapt refresh to each feed' });
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
    await user.click(screen.getAllByRole('button', { name: /^Output/ })[0]);
    await user.click(toggle);
    const save = await screen.findByRole('button', { name: 'Save changes' });
    await user.click(save);
    await waitFor(() => expect(updateSettings).toHaveBeenCalledWith(
      expect.objectContaining({ adaptiveRefreshEnabled: false }),
    ));
  });

  it('saves retention drafts from its own button, not the sticky bar', async () => {
    const user = userEvent.setup();
    renderSettings();
    await screen.findByRole('heading', { name: 'Global defaults' });
    await user.type(screen.getByRole('textbox', { name: 'Search settings' }), 'never-downloaded');
    const input = await screen.findByLabelText('Keep never-downloaded episodes for:');
    await waitFor(() => expect(input).toHaveProperty('disabled', false));
    await user.clear(input);
    await user.type(input, '21');
    expect(screen.getByRole('button', { name: 'Save retention settings' })).toBeDefined();
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
  });
});
