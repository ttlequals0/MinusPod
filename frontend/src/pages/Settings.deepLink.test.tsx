/** Settings page deep links and category counts, with the real sections. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../context/ThemeContext';
import Settings from './Settings';
import type { Settings as SettingsShape, SettingValue } from '../api/types';
import { makeFailoverTarget } from '../test/failover';

const nav = vi.hoisted(() => ({ hash: '' }));
vi.mock('react-router', () => ({
  useLocation: () => ({ hash: nav.hash, pathname: '/settings', search: '', key: nav.hash }),
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
vi.mock('./settings/Podcasting20Section', () => ({ default: () => null }));
vi.mock('./settings/AudioCueDetectionSection', () => ({ default: () => null }));
vi.mock('./settings/PositionalPriorSection', () => ({ default: () => null }));
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
  nav.hash = '';
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'offsetParent', { configurable: true, get() { return document.body; } });
});


function panel(container: HTMLElement, id: string) {
  return container.querySelector(`[data-settings-category="${id}"]`) as HTMLElement;
}


beforeEach(() => {
  localStorage.setItem('sideCutout', 'fill');
  mockGetSettings.mockResolvedValue(makeSettings({
    whisperBackend: sv('openai-api', false),
    transcribeRawApiChunks: sv(false as unknown as string, true),
    podpingEnabled: sv(false as unknown as string, true),
    adaptiveRefreshEnabled: sv(true as unknown as string, true),
  }));
});

describe('Settings deep links', () => {
  it.each([
    ['youtube', 'general'],
    ['segment-actions', 'detection'],
    ['pattern-cleanup-section', 'detection'],
    ['api-tokens', 'data'],
  ])('#%s activates the %s category', async (anchor, category) => {
    nav.hash = `#${anchor}`;
    const { container } = renderSettings();
    await waitFor(() => expect(panel(container, category).className).not.toContain('hidden'));
    for (const other of ['general', 'ai', 'detection', 'output', 'data'].filter((c) => c !== category)) {
      expect(panel(container, other).className).toContain('hidden');
    }
    const target = document.getElementById(anchor) as HTMLElement;
    expect(panel(container, category).contains(target)).toBe(true);
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
  });

  it('derives the category for an anchor outside any lookup table', async () => {
    const client = makeClient();
    const { container, rerender } = renderSettings(client);
    await screen.findByRole('heading', { name: 'Global defaults' });
    const anchor = document.createElement('div');
    anchor.id = 'custom-anchor';
    panel(container, 'output').appendChild(anchor);
    nav.hash = '#custom-anchor';
    rerender(
      <QueryClientProvider client={client}>
        <ThemeProvider><Settings /></ThemeProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(panel(container, 'output').className).not.toContain('hidden'));
  });
});

describe('Settings category counts', () => {
  it('equal the section cards rendered in each panel', async () => {
    const { container } = renderSettings();
    await screen.findByRole('heading', { name: 'Global defaults' });
    const labels = { general: 'General', ai: 'AI and transcription', detection: 'Detection', output: 'Output', data: 'Data and security' };
    let total = 0;
    for (const [id, label] of Object.entries(labels)) {
      const cards = Array.from(panel(container, id).querySelectorAll('[data-search-key]'))
        .filter((c) => !c.parentElement?.closest('[data-search-key]')).length;
      total += cards;
      expect(screen.getAllByRole('button', { name: new RegExp(`^${label} \\(${cards}\\)`) }).length).toBeGreaterThan(0);
    }
    expect(total).toBeGreaterThan(0);
  });
});
